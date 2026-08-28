# -*- coding: utf-8 -*-
"""
auth.py —— 微信扫码登录

只负责"登录"这一件事：
    1. 请求二维码（POST ilink/bot/get_bot_qrcode?bot_type=3）
    2. 终端显示二维码
    3. 长轮询二维码状态（GET ilink/bot/get_qrcode_status，35 秒）
    4. 处理官方全部状态：
           wait                  —— 等待扫码，继续轮询
           scaned                —— 已扫码，等待手机确认
           need_verifycode       —— 需要从终端输入手机微信上显示的数字
           verify_code_blocked   —— 验证码错误次数过多，刷新二维码
           expired               —— 二维码过期，自动刷新（最多 3 次）
           binded_redirect       —— 该微信已绑定过此客户端
           scaned_but_redirect   —— 机房重定向，切换 base_url 继续轮询
           confirmed             —— 登录成功，返回 bot_token 等凭证
    5. 返回 WeChatAccount（由 gateway 负责持久化）

协议细节全部对照腾讯官方 openclaw-weixin 的 src/auth/login-qr.ts 实现。
"""
import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass

from config import (
    WECHAT_BASE_URL,
    WECHAT_BOT_TYPE,
    WECHAT_LOGIN_TIMEOUT,
    WECHAT_QR_POLL_TIMEOUT,
    WECHAT_QR_REFRESH_MAX,
)
from wechat.account import WeChatAccount, load_account
from wechat.util import redact_token

logger = logging.getLogger(__name__)

# 二维码状态轮询间隔（秒）：官方 waitForWeixinLogin 里每次轮询后 sleep 1000ms
QR_POLL_INTERVAL = 1.0


@dataclass
class LoginResult:
    """登录结果：confirmed 状态服务器返回的全部凭证。"""

    connected: bool
    message: str = ""
    account: WeChatAccount | None = None
    already_connected: bool = False


class LoginError(Exception):
    """登录流程失败（二维码多次过期 / 服务器异常等）。"""


class WeChatAuth:
    """微信扫码登录器。

    用法：
        auth = WeChatAuth()
        result = auth.login_with_qr()   # 阻塞直到登录成功 / 失败 / 超时
    """

    def __init__(
        self,
        base_url: str = WECHAT_BASE_URL,
        bot_type: str = WECHAT_BOT_TYPE,
        poll_timeout: float = WECHAT_QR_POLL_TIMEOUT,
        login_timeout: float = WECHAT_LOGIN_TIMEOUT,
        qr_refresh_max: int = WECHAT_QR_REFRESH_MAX,
    ):
        self.base_url = base_url
        self.bot_type = bot_type
        self.poll_timeout = poll_timeout
        self.login_timeout = login_timeout
        self.qr_refresh_max = qr_refresh_max

    # ------------------------------------------------------------------
    # 底层 HTTP（登录接口不需要 Authorization 头，所以不依赖 client.py）
    # ------------------------------------------------------------------

    def _request(self, method: str, url: str, body: dict | None = None,
                 timeout: float | None = None) -> dict:
        """发一个简单的 JSON 请求，返回解析后的 dict。

        登录接口和业务接口不同：不需要 Bearer token，
        官方 login-qr.js 里 fetchQRCode 也只带了 Content-Type。
        """
        headers = {"Content-Type": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            raise LoginError(f"HTTP {e.code}: {raw[:200]}") from e
        except urllib.error.URLError as e:
            raise LoginError(f"Network error: {e.reason}") from e

    # ------------------------------------------------------------------
    # 二维码获取 / 状态轮询
    # ------------------------------------------------------------------

    def fetch_qrcode(self) -> dict:
        """请求一个新二维码。

        对应官方 fetchQRCode：
            POST ilink/bot/get_bot_qrcode?bot_type=3
            body: {"local_token_list": [本地已有 token 列表]}

        本地已有 token 会提交给服务器，服务器据此判断该 OpenClaw 是否已绑定。
        """
        url = f"{self.base_url}/ilink/bot/get_bot_qrcode?bot_type={urllib.parse.quote(self.bot_type)}"

        # 收集本地已保存的 token（最多 10 个，官方 getLocalBotTokenList 同样限制）
        local_tokens = []
        existing = load_account()
        if existing and existing.token:
            local_tokens.append(existing.token)

        logger.info(f"Fetching QR code from {self.base_url} (bot_type={self.bot_type}, "
                    f"local_token_list count={len(local_tokens)})")
        resp = self._request("POST", url, body={"local_token_list": local_tokens})

        if not resp.get("qrcode"):
            raise LoginError(f"Server returned no qrcode: {json.dumps(resp, ensure_ascii=False)[:200]}")
        return resp

    def poll_qrcode_status(self, qrcode: str, verify_code: str | None = None) -> dict:
        """长轮询二维码状态（35 秒超时）。

        对应官方 pollQRStatus：
            GET ilink/bot/get_qrcode_status?qrcode=xxx[&verify_code=xxx]

        官方在客户端超时 / 网关错误时都返回 {"status": "wait"} 继续轮询，
        这里同样处理：超时不抛异常，返回 wait。
        """
        endpoint = f"ilink/bot/get_qrcode_status?qrcode={urllib.parse.quote(qrcode)}"
        if verify_code:
            endpoint += f"&verify_code={urllib.parse.quote(verify_code)}"
        url = f"{self.base_url}/{endpoint}"

        try:
            return self._request("GET", url, timeout=self.poll_timeout)
        except LoginError as e:
            # 网络抖动/网关超时：和官方一致，视为 wait 继续轮询
            logger.warning(f"QR status poll failed, will retry: {e}")
            return {"status": "wait"}

    # ------------------------------------------------------------------
    # 终端展示二维码
    # ------------------------------------------------------------------

    @staticmethod
    def display_qrcode(qrcode_url: str) -> None:
        """在终端显示二维码 + 备用链接。

        对应官方 displayQRCode：
            1. 用 qrcode 库把 URL 渲染成终端二维码
            2. 同时打印链接作为备用（二维码显示不出来时点链接）

        注意：qrcode_img_content 字段虽然名字带 img，实际是一个 URL 字符串，
        微信扫这个二维码会直接弹出登录确认页。
        """
        print()
        try:
            import qrcode

            qr = qrcode.QRCode(border=1)
            qr.add_data(qrcode_url)
            qr.make()
            qr.print_ascii(invert=True)
        except Exception as e:
            # qrcode 库缺失或终端不支持时，退化为只打印链接
            logger.debug(f"Terminal QR render failed, fallback to link only: {e}")

        print(f"若二维码无法显示或无法使用，请复制以下链接到浏览器打开：")
        print(f"    {qrcode_url}")
        print()

    # ------------------------------------------------------------------
    # 完整登录流程
    # ------------------------------------------------------------------

    def login_with_qr(self) -> LoginResult:
        """完整扫码登录流程（阻塞调用）。

        流程（对照官方 startWeixinLoginWithQr + waitForWeixinLogin）：
            fetch_qrcode → 显示二维码 → 轮询状态 → confirmed 返回凭证
            期间处理 expired 自动刷新（最多 3 次）、need_verifycode 终端输入等。

        返回值：
            connected=True  且带 account   → 登录成功
            already_connected=True          → 该微信已绑定过此客户端
            connected=False                 → 失败（多次过期/超时/服务器异常）
        """
        import time

        logger.info("QR login started")
        print("\n" + "=" * 50)
        print("微信扫码登录")
        print("=" * 50)

        # 1. 获取第一个二维码
        try:
            qr_resp = self.fetch_qrcode()
        except LoginError as e:
            logger.error(f"Failed to start login: {e}")
            return LoginResult(connected=False, message=f"获取二维码失败: {e}")

        qrcode = qr_resp.get("qrcode", "")
        print("请用手机微信扫描以下二维码，以继续连接：")
        self.display_qrcode(qr_resp.get("qrcode_img_content", ""))

        # 2. 轮询状态（deadline 控制总时长）
        deadline = time.monotonic() + self.login_timeout
        verify_code: str | None = None
        qr_refresh_count = 1
        scanned_printed = False
        effective_base_url = self.base_url

        while time.monotonic() < deadline:
            status_resp = self.poll_qrcode_status(qrcode, verify_code)
            status = status_resp.get("status", "")
            logger.debug(f"QR status: {status}")

            if status == "wait":
                # 等待扫码：静默继续
                pass

            elif status == "scaned":
                # 已扫码，等待手机确认
                if verify_code:
                    # 验证码被接受，清除暂存（官方同样处理）
                    logger.info("Verify code accepted, resuming polling")
                    verify_code = None
                if not scanned_printed:
                    print("\n已扫码，请在手机上确认……")
                    scanned_printed = True

            elif status == "need_verifycode":
                # 服务器要求输入手机微信上显示的数字验证码
                prompt = ("❌ 你输入的数字不匹配，请重新输入：" if verify_code
                          else "请输入手机微信上显示的数字，以继续连接：")
                verify_code = input(prompt).strip()
                continue  # 立即用新验证码重新轮询，不等间隔

            elif status == "expired":
                # 二维码过期：自动刷新（官方最多 3 次）
                qr_refresh_count += 1
                if qr_refresh_count > self.qr_refresh_max:
                    logger.warning(f"QR expired {self.qr_refresh_max} times, giving up")
                    return LoginResult(connected=False, message="二维码多次失效，请稍后再试。")
                print(f"\n⏳ 二维码已过期，正在刷新…({qr_refresh_count}/{self.qr_refresh_max})")
                try:
                    qr_resp = self.fetch_qrcode()
                except LoginError as e:
                    logger.error(f"Failed to refresh QR: {e}")
                    return LoginResult(connected=False, message=f"刷新二维码失败: {e}")
                qrcode = qr_resp.get("qrcode", "")
                verify_code = None
                scanned_printed = False
                print("🔄 二维码已更新，请重新扫描。")
                self.display_qrcode(qr_resp.get("qrcode_img_content", ""))

            elif status == "verify_code_blocked":
                # 验证码错误次数过多：刷新二维码（官方同样处理）
                logger.warning("Verify code blocked, refreshing QR")
                print("\n⛔ 验证码错误次数过多，正在刷新二维码……")
                qr_refresh_count += 1
                if qr_refresh_count > self.qr_refresh_max:
                    return LoginResult(connected=False, message="验证码多次输入错误，请稍后再试。")
                try:
                    qr_resp = self.fetch_qrcode()
                except LoginError as e:
                    return LoginResult(connected=False, message=f"刷新二维码失败: {e}")
                qrcode = qr_resp.get("qrcode", "")
                verify_code = None
                scanned_printed = False
                self.display_qrcode(qr_resp.get("qrcode_img_content", ""))

            elif status == "binded_redirect":
                # 该微信已绑定过此客户端，无需重复连接（官方返回 alreadyConnected）
                logger.info("Bot already bound to this client")
                print("\n✅ 此微信已连接过本程序，无需重复连接。")
                return LoginResult(connected=False, already_connected=True,
                                   message="已连接过本程序，无需重复连接。")

            elif status == "scaned_but_redirect":
                # 机房重定向：切换轮询域名（官方 currentApiBaseUrl 机制）
                redirect_host = status_resp.get("redirect_host")
                if redirect_host:
                    effective_base_url = f"https://{redirect_host}"
                    logger.info(f"IDC redirect, switching polling host to {redirect_host}")
                else:
                    logger.warning("scaned_but_redirect received but redirect_host missing")

            elif status == "confirmed":
                # 登录成功：返回 bot_token 等凭证
                ilink_bot_id = status_resp.get("ilink_bot_id")
                if not ilink_bot_id:
                    logger.error("Login confirmed but ilink_bot_id missing from response")
                    return LoginResult(connected=False, message="登录失败：服务器未返回 ilink_bot_id。")

                account = WeChatAccount(
                    account_id=ilink_bot_id,
                    token=status_resp.get("bot_token", ""),
                    base_url=status_resp.get("baseurl") or effective_base_url,
                    user_id=status_resp.get("ilink_user_id", ""),
                )
                logger.info(
                    f"Login confirmed! ilink_bot_id={account.account_id} "
                    f"ilink_user_id={redact_token(account.user_id)} "
                    f"token={redact_token(account.token)}"
                )
                print("\n✅ 登录成功！微信已连接。")
                return LoginResult(connected=True, message="登录成功。", account=account)

            else:
                # 未知状态：记录日志继续轮询，避免因为协议新增状态而卡死
                logger.warning(f"Unknown QR status: {status}, keep polling")

            time.sleep(QR_POLL_INTERVAL)

        logger.warning("QR login timed out")
        return LoginResult(connected=False, message="登录超时，请重试。")
