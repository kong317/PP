# -*- coding: utf-8 -*-
"""
account.py —— 微信账号凭证管理

负责登录凭证 / 消息游标 / context_token 的本地持久化。

对照官方 openclaw-weixin 的设计：
    - src/auth/accounts.js：每个账号存 {token, baseUrl, userId, savedAt}，权限 0600
    - src/storage/sync-buf.js：get_updates_buf 游标单独存文件，跨重启续传
    - src/messaging/inbound.js：contextToken 按 (accountId, userId) 缓存并落盘

我们的布局（第一阶段只支持单账号）：
    data/wechat/
        account.json        —— 登录凭证（token 等敏感信息）
        sync_buf.json       —— getUpdates 游标
        context_tokens.json —— 每个用户最近一条消息的 context_token

安全要求：
    - token 不写死在代码里
    - data/ 目录已在 .gitignore 中，凭证不会提交到 Git
    - token 不进日志（打码见 util.py）
"""
import json
import logging
import os
from dataclasses import dataclass, asdict

from config import WECHAT_DATA_DIR, WECHAT_BASE_URL
from wechat.util import redact_token

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 账号数据模型
# ---------------------------------------------------------------------------


@dataclass
class WeChatAccount:
    """一个已登录微信 Bot 账号的全部凭证信息。

    字段均来自二维码登录 confirmed 状态的服务器返回（官方 login-qr.ts）：
        account_id = ilink_bot_id   （形如 xxx@im.bot）
        token      = bot_token
        base_url   = baseurl        （后续 API 请求的域名，可能因机房重定向变化）
        user_id    = ilink_user_id  （登录者的微信用户 ID，形如 xxx@im.wechat）
    """

    account_id: str
    token: str
    base_url: str = WECHAT_BASE_URL
    user_id: str = ""
    saved_at: str = ""

    def __post_init__(self):
        # baseurl 可能为空或纯空白，回退到固定域名（官方 resolveWeixinAccount 同样处理）
        if not self.base_url or not self.base_url.strip():
            self.base_url = WECHAT_BASE_URL


# ---------------------------------------------------------------------------
# 账号凭证读写
# ---------------------------------------------------------------------------


def _account_path() -> str:
    """data/wechat/account.json"""
    return os.path.join(WECHAT_DATA_DIR, "account.json")


def save_account(account: WeChatAccount) -> None:
    """保存（覆盖）登录凭证到本地。

    参照官方做法尽力把文件权限设为 0600（Windows 上基本是 no-op，但无害）。
    """
    os.makedirs(WECHAT_DATA_DIR, exist_ok=True)
    path = _account_path()

    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(account), f, ensure_ascii=False, indent=2)

    try:
        os.chmod(path, 0o600)  # best-effort，和官方一致
    except OSError:
        pass

    # 注意：日志里绝不打印完整 token
    logger.info(f"Account saved: id={account.account_id} token={redact_token(account.token)}")


def load_account() -> WeChatAccount | None:
    """读取本地凭证；文件不存在或损坏时返回 None（触发重新扫码登录）。"""
    path = _account_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        account = WeChatAccount(
            account_id=data.get("account_id", ""),
            token=data.get("token", ""),
            base_url=data.get("base_url", "") or WECHAT_BASE_URL,
            user_id=data.get("user_id", ""),
            saved_at=data.get("saved_at", ""),
        )
        if not account.token:
            logger.warning("Account file exists but token is empty, treating as not logged in")
            return None
        return account
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to load account file ({e}), treating as not logged in")
        return None


def clear_account() -> None:
    """删除本地凭证（token 失效、用户主动登出时调用）。"""
    path = _account_path()
    try:
        os.remove(path)
        logger.info("Account credentials cleared (re-login required)")
    except FileNotFoundError:
        pass


# ---------------------------------------------------------------------------
# getUpdates 游标（get_updates_buf）持久化
# ---------------------------------------------------------------------------
# 游标是 getUpdates 长轮询的关键：服务器靠它记住"发到哪里了"。
# 必须落盘，否则程序重启后会重复收到历史消息（官方 sync-buf.js 同样持久化）。


def load_sync_buf() -> str:
    """读取上次的 get_updates_buf；首次运行返回空字符串。"""
    path = os.path.join(WECHAT_DATA_DIR, "sync_buf.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        buf = data.get("get_updates_buf")
        return buf if isinstance(buf, str) else ""
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ""


def save_sync_buf(buf: str) -> None:
    """保存新的 get_updates_buf（只在非空时调用，和官方 monitor.js 一致）。"""
    os.makedirs(WECHAT_DATA_DIR, exist_ok=True)
    path = os.path.join(WECHAT_DATA_DIR, "sync_buf.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"get_updates_buf": buf}, f)


# ---------------------------------------------------------------------------
# context_token 持久化
# ---------------------------------------------------------------------------
# 微信要求：回复某条消息时，必须原样回传该消息携带的 context_token，
# 否则回复无法关联到正确的会话。token 每条消息都会更新，所以要按用户缓存，
# 并且落盘（官方 inbound.js 的 contextTokenStore 同样做内存+磁盘双份）。


def load_context_tokens() -> dict:
    """读取 {user_id: context_token} 映射；文件不存在返回空 dict。"""
    path = os.path.join(WECHAT_DATA_DIR, "context_tokens.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def save_context_tokens(tokens: dict) -> None:
    """把 {user_id: context_token} 映射整体写回磁盘。"""
    os.makedirs(WECHAT_DATA_DIR, exist_ok=True)
    path = os.path.join(WECHAT_DATA_DIR, "context_tokens.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tokens, f, ensure_ascii=False)
