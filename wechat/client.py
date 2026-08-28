# -*- coding: utf-8 -*-
"""
client.py —— 腾讯微信 iLink Bot API 通信层

WeChatClient 只负责：HTTP + JSON + 腾讯 iLink 协议。
绝对不放：LLM / Memory / Conversation / Companion 人格 / 任何业务逻辑。

协议细节全部对照腾讯官方 openclaw-weixin 的 src/api/api.ts（v2.4.6）：

请求头（所有 POST 请求，官方 buildHeaders）：
    Content-Type: application/json
    AuthorizationType: ilink_bot_token
    Authorization: Bearer <bot_token>
    X-WECHAT-UIN: 随机 uint32 → 十进制字符串 → base64（每次请求都变，防重放）
    iLink-App-Id: bot
    iLink-App-ClientVersion: major<<16 | minor<<8 | patch

接口（base_url 默认为 https://ilinkai.weixin.qq.com，登录后以 baseurl 为准）：
    POST ilink/bot/getupdates      长轮询收消息（默认 35 秒超时）
    POST ilink/bot/sendmessage     发送消息
    POST ilink/bot/getconfig       获取账号配置（含 typing_ticket）
    POST ilink/bot/sendtyping      发送/取消"正在输入"
    POST ilink/bot/getuploadurl    获取 CDN 上传地址（第一阶段预留）
    POST ilink/bot/msg/notifystart 通知服务器本客户端启动
    POST ilink/bot/msg/notifystop  通知服务器本客户端停止

每个请求体都会附带 base_info（官方 buildBaseInfo）：
    {"channel_version": "...", "bot_agent": "..."}
"""
import base64
import json
import logging
import random
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any

from config import (
    WECHAT_API_TIMEOUT,
    WECHAT_BOT_AGENT,
    WECHAT_CLIENT_VERSION,
    WECHAT_CONFIG_TIMEOUT,
)
from wechat.util import redact_json_body, redact_token

logger = logging.getLogger(__name__)

# 官方 api.ts 中的超时默认值
DEFAULT_LONG_POLL_TIMEOUT_MS = 35_000
DEFAULT_API_TIMEOUT_MS = 15_000
DEFAULT_CONFIG_TIMEOUT_MS = 10_000

# 官方 session-guard.js：errcode = -14 表示 bot_token 过期/失效
STALE_TOKEN_ERRCODE = -14


class WeChatApiError(Exception):
    """API 返回业务错误（响应里 ret / errcode 非 0）。"""

    def __init__(self, ret=None, errcode=None, errmsg=""):
        self.ret = ret
        self.errcode = errcode
        self.errmsg = errmsg
        super().__init__(f"ret={ret} errcode={errcode} errmsg={errmsg}")

    @property
    def is_stale_token(self) -> bool:
        """token 是否已失效（官方 STALE_TOKEN_ERRCODE = -14）。"""
        return self.errcode == STALE_TOKEN_ERRCODE or self.ret == STALE_TOKEN_ERRCODE


class WeChatHttpError(Exception):
    """HTTP 层错误（非 2xx 状态码）。"""

    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body[:200]}")


def build_client_version_uint(version: str) -> int:
    """把 "1.2.3" 编码成 uint32：major<<16 | minor<<8 | patch。

    对照官方 api.ts 的 buildClientVersion：
        "1.0.11" -> 0x0001000B = 65547
    """
    parts = version.split(".")
    major = int(parts[0] or 0)
    minor = int(parts[1] or 0)
    patch = int(parts[2] or 0)
    return ((major & 0xFF) << 16) | ((minor & 0xFF) << 8) | (patch & 0xFF)


class WeChatClient:
    """腾讯微信 iLink Bot API 客户端（纯 HTTP 通信）。"""

    def __init__(
        self,
        base_url: str,
        token: str,
        api_timeout: float = WECHAT_API_TIMEOUT,
        config_timeout: float = WECHAT_CONFIG_TIMEOUT,
        client_version: str = WECHAT_CLIENT_VERSION,
        bot_agent: str = WECHAT_BOT_AGENT,
    ):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.api_timeout = api_timeout
        self.config_timeout = config_timeout
        # iLink-App-ClientVersion 请求头：uint32 版本号
        self._client_version_uint = build_client_version_uint(client_version)
        self._channel_version = client_version
        self._bot_agent = bot_agent

    # ------------------------------------------------------------------
    # 请求构造
    # ------------------------------------------------------------------

    @staticmethod
    def _random_wechat_uin() -> str:
        """生成 X-WECHAT-UIN 请求头：随机 uint32 → 十进制字符串 → base64。

        对照官方 api.ts 的 randomWechatUin：
            crypto.randomBytes(4).readUInt32BE(0) → String(uint32) → base64
        """
        uint32 = random.getrandbits(32)  # 0 ~ 2^32-1
        return base64.b64encode(str(uint32).encode("utf-8")).decode("ascii")

    def _build_headers(self) -> dict:
        """构造所有 POST 请求的公共头（对照官方 buildHeaders）。"""
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self.token}",
            # 每次请求都随机，起防重放作用
            "X-WECHAT-UIN": self._random_wechat_uin(),
            # 官方 package.json 中 ilink_appid = "bot"
            "iLink-App-Id": "bot",
            "iLink-App-ClientVersion": str(self._client_version_uint),
        }

    def _build_base_info(self) -> dict:
        """每个请求体里的 base_info（对照官方 buildBaseInfo）。"""
        return {
            "channel_version": self._channel_version,
            "bot_agent": self._bot_agent,
        }

    def _post(self, endpoint: str, body: dict, timeout: float) -> dict:
        """POST JSON 到指定端点，返回解析后的 dict。

        只处理传输层：HTTP 错误抛 WeChatHttpError，网络错误向上抛 URLError。
        """
        url = f"{self.base_url}/{endpoint}"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            url, data=data, headers=self._build_headers(), method="POST"
        )
        logger.debug(f"POST {url} body={redact_json_body(json.dumps(body, ensure_ascii=False))}")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            logger.error(f"POST {url} failed: HTTP {e.code}")
            raise WeChatHttpError(e.code, raw) from e

        logger.debug(f"POST {url} resp={redact_json_body(raw)}")
        return json.loads(raw)

    # ------------------------------------------------------------------
    # 核心接口
    # ------------------------------------------------------------------

    def get_updates(self, get_updates_buf: str = "",
                    timeout_ms: int = DEFAULT_LONG_POLL_TIMEOUT_MS) -> dict:
        """长轮询获取新消息（Gateway 主循环的核心）。

        对照官方 api.ts 的 getUpdates：
            POST ilink/bot/getupdates
            body: {"get_updates_buf": "<游标>", "base_info": {...}}

        服务器会 hold 住连接最多 35 秒（longpolling_timeout_ms），
        有新消息或超时后返回。响应结构：
            {
                "ret": 0,
                "msgs": [...],
                "get_updates_buf": "<新游标>",      # 必须保存并下次回传
                "longpolling_timeout_ms": 35000     # 服务器建议的下次超时
            }

        注意：客户端超时（URLError timeout）由 gateway 捕获后直接重试，
        这是长轮询的正常控制流，不算错误。
        """
        # 客户端超时要略大于服务器 hold 时间，给网络留余量
        client_timeout = timeout_ms / 1000.0 + 5.0

        resp = self._post(
            "ilink/bot/getupdates",
            {
                "get_updates_buf": get_updates_buf,
                "base_info": self._build_base_info(),
            },
            timeout=client_timeout,
        )
        return resp

    def send_message(
        self,
        to_user_id: str,
        text: str,
        context_token: str | None = None,
        client_id: str | None = None,
    ) -> None:
        """发送一条文本消息。

        对照官方 send.js 的 buildTextMessageReq + api.ts 的 sendMessage：
            POST ilink/bot/sendmessage
            body: {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": "<目标用户>",
                    "client_id": "<本客户端生成的唯一ID>",
                    "message_type": 2,          # BOT
                    "message_state": 2,         # FINISH
                    "item_list": [{"type": 1, "text_item": {"text": "..."}}],
                    "context_token": "<收到消息时服务器下发的 token>"  # 关键！
                },
                "base_info": {...}
            }

        context_token 必须回传收到的那条消息携带的 token，
        否则回复无法关联到正确会话（官方明确要求）。
        """
        if client_id is None:
            # 官方 generateId 格式：{prefix}:{timestamp}-{8位hex}
            client_id = f"pp:{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"

        msg = {
            "from_user_id": "",
            "to_user_id": to_user_id,
            "client_id": client_id,
            "message_type": 2,   # BOT（对照官方 MessageType）
            "message_state": 2,  # FINISH（对照官方 MessageState）
            "item_list": [
                {"type": 1, "text_item": {"text": text}}  # type=1 即 TEXT
            ],
        }
        # 官方在 context_token 缺失时直接省略该字段（JSON.stringify 会丢 undefined）
        if context_token:
            msg["context_token"] = context_token

        body = {
            "msg": msg,
            "base_info": self._build_base_info(),
        }

        resp = self._post("ilink/bot/sendmessage", body, timeout=self.api_timeout)

        # 官方：resp.ret 非 0 时抛错
        if resp.get("ret") not in (None, 0):
            raise WeChatApiError(
                ret=resp.get("ret"),
                errcode=resp.get("errcode"),
                errmsg=resp.get("errmsg", ""),
            )

    def get_config(self, ilink_user_id: str, context_token: str | None = None) -> dict:
        """获取账号配置（含 typing_ticket，用于 sendTyping）。

        对照官方 api.ts 的 getConfig：
            POST ilink/bot/getconfig
            body: {"ilink_user_id": ..., "context_token": ..., "base_info": {...}}
        """
        body = {
            "ilink_user_id": ilink_user_id,
            "base_info": self._build_base_info(),
        }
        if context_token:
            body["context_token"] = context_token

        return self._post("ilink/bot/getconfig", body, timeout=self.config_timeout)

    def send_typing(self, ilink_user_id: str, typing_ticket: str, status: int) -> None:
        """发送/取消"正在输入"状态。

        对照官方 process-message.js 里的 sendTyping 调用：
            POST ilink/bot/sendtyping
            body: {"ilink_user_id": ..., "typing_ticket": ...,
                   "status": 1或2, "base_info": {...}}
        status: 1 = TYPING，2 = CANCEL（官方 TypingStatus）
        """
        self._post(
            "ilink/bot/sendtyping",
            {
                "ilink_user_id": ilink_user_id,
                "typing_ticket": typing_ticket,
                "status": status,
                "base_info": self._build_base_info(),
            },
            timeout=self.config_timeout,
        )

    def get_upload_url(self, **params: Any) -> dict:
        """获取 CDN 预签名上传地址（第一阶段预留，媒体消息后续实现）。

        对照官方 api.ts 的 getUploadUrl（参数名保持官方命名）：
            POST ilink/bot/getuploadurl
            body: {filekey, media_type, to_user_id, rawsize, rawfilemd5,
                   filesize, thumb_rawsize, thumb_rawfilemd5, thumb_filesize,
                   no_need_thumb, aeskey, base_info}
        """
        params["base_info"] = self._build_base_info()
        return self._post("ilink/bot/getuploadurl", params, timeout=self.api_timeout)

    def notify_start(self) -> None:
        """通知服务器本客户端启动（官方 channel.js 的 startAccount 调用）。"""
        try:
            resp = self._post(
                "ilink/bot/msg/notifystart",
                {"base_info": self._build_base_info()},
                timeout=self.config_timeout,
            )
            if resp.get("ret") not in (None, 0):
                logger.warning(f"notifyStart ret={resp.get('ret')} errmsg={resp.get('errmsg', '')}")
        except (WeChatHttpError, urllib.error.URLError) as e:
            # 官方同样忽略启动通知失败
            logger.warning(f"notifyStart failed during startup (ignored): {e}")

    def notify_stop(self) -> None:
        """通知服务器本客户端停止（官方 channel.js 的 stopAccount 调用）。"""
        try:
            resp = self._post(
                "ilink/bot/msg/notifystop",
                {"base_info": self._build_base_info()},
                timeout=self.config_timeout,
            )
            if resp.get("ret") not in (None, 0):
                logger.warning(f"notifyStop ret={resp.get('ret')} errmsg={resp.get('errmsg', '')}")
        except (WeChatHttpError, urllib.error.URLError) as e:
            logger.warning(f"notifyStop failed during shutdown (ignored): {e}")
