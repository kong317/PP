# -*- coding: utf-8 -*-
"""
gateway.py —— 微信网关（微信 ↔ Companion 的通信桥梁）

职责边界（重要）：
    Gateway 只负责：
        微信登录状态管理
        getUpdates 长轮询
        微信原始消息 → Message 适配
        Companion.chat(user_id, message) 调用
        回复 → 微信 sendMessage
        异常恢复 / 重连 / token 失效处理

    Gateway 绝不负责：
        LLM 实现 / Memory 实现 / 数据库实现 / 任何 AI 逻辑

数据流：
    微信
      ↓
    WeChatClient.get_updates（长轮询）
      ↓
    message.from_weixin 适配器 → Message
      ↓
    Companion.chat(message.sender_id, message.content)
      ↓
    WeChatClient.send_message（携带 context_token）
      ↓
    微信

主循环对照官方 monitor.js 的 monitorWeixinProvider：
    - 游标（get_updates_buf）从磁盘加载，每次响应后落盘（跨重启续传）
    - 服务器通过 longpolling_timeout_ms 动态调整下次长轮询超时
    - 连续失败计数：普通错误 2 秒重试，连续 3 次退避 30 秒
    - errcode = -14（token 失效）→ 清理凭证 → 重新扫码登录
"""
import logging
import time
import urllib.error

from config import (
    WECHAT_BASE_URL,
    WECHAT_LOG_MESSAGE_CONTENT,
    WECHAT_POLL_TIMEOUT_MS,
)
from wechat.account import (
    WeChatAccount,
    clear_account,
    load_account,
    load_context_tokens,
    load_sync_buf,
    save_account,
    save_context_tokens,
    save_sync_buf,
)
from wechat.auth import WeChatAuth
from wechat.client import WeChatApiError, WeChatClient, WeChatHttpError
from wechat.message import Message, from_weixin
from wechat.util import redact_token

logger = logging.getLogger(__name__)

# 连续失败退避策略（对照官方 monitor.js）
MAX_CONSECUTIVE_FAILURES = 3
RETRY_DELAY = 2.0        # 普通失败重试间隔（秒）
BACKOFF_DELAY = 30.0     # 连续 3 次失败后的退避时间（秒）


class WeChatGateway:
    """微信网关主类。

    用法：
        companion = Companion()
        gateway = WeChatGateway(companion)
        gateway.start()      # 阻塞运行；Ctrl+C 退出
    """

    def __init__(self, companion, base_url: str = WECHAT_BASE_URL):
        # Companion 核心（只依赖 chat(user_id, message) 这一个接口）
        self.companion = companion
        self.base_url = base_url

        # 登录器（扫码流程）
        self.auth = WeChatAuth(base_url=base_url)

        # 当前账号 / 客户端 / 游标 / context_token 缓存
        self.account: WeChatAccount | None = None
        self.client: WeChatClient | None = None
        self._get_updates_buf: str = ""
        # {user_id: context_token}：回复消息时必须回传（落盘跨重启）
        self._context_tokens: dict = {}

        self._running = False

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动网关（阻塞直到 Ctrl+C）。

        流程：
            1. 检查本地凭证 → 有则直接用，无则扫码登录
            2. 加载游标 / context_token
            3. notify_start 通知服务器
            4. 进入长轮询主循环
            5. token 失效时：清理凭证 → 重新扫码登录 → 继续循环
        """
        logger.info("Gateway started")
        self._running = True

        try:
            while self._running:
                # 1. 确保已登录（token 失效后这里会重新走扫码流程）
                if self.client is None:
                    self._ensure_login()
                    if self.client is None:
                        # 登录失败：等待后重试（让用户有机会重新扫码）
                        time.sleep(RETRY_DELAY)
                        continue

                # 2. 进入长轮询主循环；返回 False 表示 token 失效需要重登
                try:
                    ok = self._run_poll_loop()
                    if not ok:
                        # token 失效：清理凭证，下一轮 while 重新登录
                        logger.warning("Token expired, clearing credentials and re-login")
                        self._handle_token_expired()
                except KeyboardInterrupt:
                    raise
                except Exception as e:
                    # 最外层兜底：任何未预期异常都不能让程序退出
                    logger.error(f"Unexpected error in poll loop: {e}", exc_info=True)
                    time.sleep(BACKOFF_DELAY)
        except KeyboardInterrupt:
            logger.info("Gateway stopped by user (Ctrl+C)")
        finally:
            self._shutdown()

    def _shutdown(self) -> None:
        """优雅退出：通知服务器本客户端停止。"""
        self._running = False
        if self.client:
            self.client.notify_stop()
        logger.info("Gateway stopped")

    # ------------------------------------------------------------------
    # 登录
    # ------------------------------------------------------------------

    def _ensure_login(self) -> None:
        """确保有可用凭证：本地有 → 直接用；没有 → 扫码登录并保存。"""
        account = load_account()
        if account:
            logger.info(f"Loaded saved credentials (account={account.account_id}, "
                        f"token={redact_token(account.token)}), no QR login needed")
        else:
            logger.info("No saved credentials, starting QR login")
            account = self._login_with_qr()
            if account is None:
                return  # 登录失败，保持 client=None

        self.account = account
        self.client = WeChatClient(base_url=account.base_url, token=account.token)

        # 加载游标和 context_token（跨重启续传）
        self._get_updates_buf = load_sync_buf()
        self._context_tokens = load_context_tokens()
        if self._get_updates_buf:
            logger.info(f"Resumed from previous sync buf ({len(self._get_updates_buf)} bytes)")
        else:
            logger.info("No previous sync buf, starting fresh")

        # 通知服务器本客户端上线（官方 startAccount 同样调用）
        self.client.notify_start()

    def _login_with_qr(self) -> WeChatAccount | None:
        """执行扫码登录流程，成功后保存凭证到本地。"""
        result = self.auth.login_with_qr()

        if result.connected and result.account:
            # 登录成功：立即落盘，下次启动无需再扫码
            save_account(result.account)
            logger.info("Login successful")
            return result.account

        if result.already_connected:
            # 服务器认为已绑定：本地凭证应该还在（可能被误删），
            # 下次循环会重新读取；这里只提示
            logger.info(result.message)
            return None

        logger.error(f"Login failed: {result.message}")
        return None

    def _handle_token_expired(self) -> None:
        """token 失效处理：清理本地凭证，等待重新扫码。

        用户要求（对照官方 session-guard 的 -14 处理）：
            Token invalid → 清理登录状态 → 提示重新扫码
        """
        clear_account()
        self.account = None
        self.client = None
        self._get_updates_buf = ""
        print("\n⚠️  登录凭证已失效，需要重新扫码登录。")
        logger.warning("Credentials cleared after token expiry")

    # ------------------------------------------------------------------
    # 长轮询主循环
    # ------------------------------------------------------------------

    def _run_poll_loop(self) -> bool:
        """getUpdates 长轮询主循环（对照官方 monitor.js）。

        返回值：
            True  —— 正常退出（仅在 _running 置 False 时）
            False —— token 失效，需要重新登录
        """
        consecutive_failures = 0
        # 服务器会通过 longpolling_timeout_ms 动态调整，初始用配置值
        timeout_ms = WECHAT_POLL_TIMEOUT_MS

        while self._running:
            try:
                resp = self.client.get_updates(
                    self._get_updates_buf, timeout_ms=timeout_ms
                )

                # 服务器建议的下次长轮询超时（官方 monitor.js 同样处理）
                if resp.get("longpolling_timeout_ms"):
                    timeout_ms = int(resp["longpolling_timeout_ms"])
                    logger.debug(f"Updated next poll timeout: {timeout_ms}ms")

                # 业务错误：ret / errcode 非 0
                ret = resp.get("ret")
                errcode = resp.get("errcode")
                is_api_error = (ret is not None and ret != 0) or (errcode is not None and errcode != 0)
                if is_api_error:
                    # token 失效（errcode = -14）→ 返回 False 触发重新登录
                    if errcode == -14 or ret == -14:
                        logger.error(f"getUpdates: token is stale (errcode={errcode}), re-login required")
                        return False

                    # 其他业务错误：计数 + 重试/退避
                    consecutive_failures += 1
                    errmsg = resp.get("errmsg", "")
                    logger.error(f"getUpdates failed: ret={ret} errcode={errcode} "
                                 f"errmsg={errmsg} ({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})")
                    time.sleep(self._backoff(consecutive_failures))
                    if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                        consecutive_failures = 0  # 退避后重置计数（官方 monitor.js 同样处理）
                    continue

                # 成功：重置失败计数
                consecutive_failures = 0

                # 保存新游标（非空才写，官方 monitor.js 同样处理）
                new_buf = resp.get("get_updates_buf")
                if new_buf:
                    self._get_updates_buf = new_buf
                    save_sync_buf(new_buf)

                # 逐条处理消息
                for raw_msg in resp.get("msgs") or []:
                    try:
                        self._handle_message(raw_msg)
                    except Exception as e:
                        # 单条消息处理失败绝不能拖垮整个网关
                        logger.error(f"Failed to handle message: {e}", exc_info=True)

            except KeyboardInterrupt:
                raise
            except urllib.error.URLError as e:
                # 网络错误：计数 + 重试/退避（长轮询超时也算网络错误）
                if isinstance(e.reason, TimeoutError) or "timed out" in str(e.reason).lower():
                    # 长轮询客户端超时是正常控制流：服务器 hold 35 秒没消息，
                    # 静默继续下一轮（官方 getUpdates 同样返回空结果继续）
                    logger.debug("getUpdates long-poll timeout (normal), continuing")
                    consecutive_failures = 0
                    continue
                consecutive_failures += 1
                logger.error(f"getUpdates network error: {e.reason} "
                             f"({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})")
                time.sleep(self._backoff(consecutive_failures))
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    consecutive_failures = 0
            except WeChatHttpError as e:
                # HTTP 层错误（4xx/5xx）：常见于服务器临时不可用，计数 + 退避
                consecutive_failures += 1
                logger.error(f"getUpdates HTTP error: {e} "
                             f"({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})")
                time.sleep(self._backoff(consecutive_failures))
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    consecutive_failures = 0
            except (ValueError, KeyError) as e:
                # JSON 解析失败 / 响应格式异常
                consecutive_failures += 1
                logger.error(f"getUpdates bad response: {e} "
                             f"({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})")
                time.sleep(self._backoff(consecutive_failures))
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    consecutive_failures = 0
            except WeChatApiError as e:
                # send_message 里的业务错误也可能冒到这里（极端情况）
                if e.is_stale_token:
                    return False
                consecutive_failures += 1
                logger.error(f"API error: {e} ({consecutive_failures}/{MAX_CONSECUTIVE_FAILURES})")
                time.sleep(self._backoff(consecutive_failures))
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    consecutive_failures = 0

        return True

    @staticmethod
    def _backoff(consecutive_failures: int) -> float:
        """退避策略：连续 3 次失败退避 30 秒，否则 2 秒（对照官方）。"""
        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            return BACKOFF_DELAY
        return RETRY_DELAY

    # ------------------------------------------------------------------
    # 单条消息处理
    # ------------------------------------------------------------------

    def _handle_message(self, raw_msg: dict) -> None:
        """处理一条微信原始消息：

        微信消息 → Message 适配 → Companion.chat → 回复微信。
        """
        # ① 适配：微信协议 → 内部 Message（无效消息返回 None 直接跳过）
        msg = from_weixin(raw_msg)
        if msg is None:
            item_types = ",".join(str(i.get("type")) for i in (raw_msg.get("item_list") or []))
            logger.debug(f"Skipping non-text/bot message (item_types=[{item_types}])")
            return

        # ② 更新 context_token（回复时必须原样回传，官方 setContextToken）
        context_token = raw_msg.get("context_token")
        if context_token:
            self._context_tokens[msg.sender_id] = context_token
            save_context_tokens(self._context_tokens)

        # ③ 日志：默认只记录发送者和长度，保护隐私（配置可开启完整内容）
        if WECHAT_LOG_MESSAGE_CONTENT:
            logger.info(f"Message received from={msg.sender_id} content={msg.content!r}")
        else:
            logger.info(f"Message received from={msg.sender_id} len={len(msg.content)}")

        # ④ 调用 Companion（AI 回复）
        try:
            response = self.companion.chat(msg.sender_id, msg.content)
        except Exception as e:
            # LLM 异常不能拖垮网关：记录日志 + 给用户回一条错误提示
            logger.error(f"Companion.chat failed for {msg.sender_id}: {e}", exc_info=True)
            response = "抱歉，我刚才走神了……请稍后再试一次。"

        # ⑤ 回复微信（空回复兜底，避免发空消息）
        if not response or not response.strip():
            response = "……（我什么也没想出来）"

        self.client.send_message(
            to_user_id=msg.sender_id,
            text=response,
            context_token=self._context_tokens.get(msg.sender_id),
        )
        if WECHAT_LOG_MESSAGE_CONTENT:
            logger.info(f"Message sent to={msg.sender_id} content={response!r}")
        else:
            logger.info(f"Message sent to={msg.sender_id} len={len(response)}")

    # ------------------------------------------------------------------
    # 测试工具
    # ------------------------------------------------------------------

    def send_test_message(self, text: str = "这是一条测试消息 🎉") -> bool:
        """向登录者本人（ilink_user_id）发送一条测试消息。

        对应验收标准里的 Test 5：Python sendMessage → 微信收到测试消息。
        前提：本地已有登录凭证。
        """
        account = load_account()
        if not account:
            print("尚未登录，请先运行: python main.py wechat")
            return False

        client = WeChatClient(base_url=account.base_url, token=account.token)
        if not account.user_id:
            print("凭证中缺少 ilink_user_id，无法发送测试消息")
            return False

        tokens = load_context_tokens()
        try:
            client.send_message(
                to_user_id=account.user_id,
                text=text,
                context_token=tokens.get(account.user_id),
            )
            print(f"测试消息已发送给 {account.user_id}")
            return True
        except Exception as e:
            print(f"发送失败: {e}")
            return False
