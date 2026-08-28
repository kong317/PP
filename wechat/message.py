# -*- coding: utf-8 -*-
"""
message.py —— 项目内部统一消息模型（消息标准化层）

设计原则（消息标准化）：
    微信返回的原始 JSON 结构（腾讯 iLink 协议）绝对不允许直接进入 Companion。
    Companion 只认识 Message，不认识微信。

    腾讯微信 API 原始消息
            │
            ▼
        from_weixin() 适配器（本文件）
            │
            ▼
        Message（项目内部标准消息）
            │
            ▼
        Companion.chat(user_id, content)

以后如果接入 Telegram / Web / QQ 等渠道，只需要为新渠道再写一个适配器，
Companion 一行都不用改。
"""
from dataclasses import dataclass


# 微信 MessageItem 的类型枚举（对照官方 src/api/types.ts）
# 第一阶段只处理 TEXT（type=1），其余类型留作后续扩展
MESSAGE_ITEM_TEXT = 1
MESSAGE_ITEM_IMAGE = 2
MESSAGE_ITEM_VOICE = 3
MESSAGE_ITEM_FILE = 4
MESSAGE_ITEM_VIDEO = 5

# 消息方向（message_type）：1=用户发来，2=机器人发出
MESSAGE_TYPE_USER = 1
MESSAGE_TYPE_BOT = 2


@dataclass
class Message:
    """项目内部统一消息模型。

    Companion 只依赖这个类，不依赖任何微信协议字段。
    """

    # 发送者唯一 ID（微信里形如 xxx@im.wechat；终端模式下为 "terminal"）
    sender_id: str

    # 文本内容（第一阶段只支持文本）
    content: str

    # 发送者昵称（微信 API 不直接提供昵称，先留空）
    sender_name: str = ""

    # 平台侧消息 ID（微信的 message_id），用于排重/日志
    message_id: str = ""

    # 消息时间戳（毫秒，来自微信 create_time_ms）
    timestamp: int = 0

    # 会话 ID（微信的 session_id），暂未使用，先保留
    session_id: str = ""


def from_weixin(raw: dict) -> Message | None:
    """把微信 getUpdates 返回的原始消息转换成内部 Message。

    参数 raw 是 getupdates 响应 msgs 数组中的一条 WeixinMessage，例如：

        {
            "seq": 1,
            "message_id": "123456",
            "from_user_id": "abc@im.wechat",
            "to_user_id": "xyz@im.bot",
            "create_time_ms": 1756348800000,
            "session_id": "...",
            "message_type": 1,
            "message_state": 2,
            "item_list": [
                {"type": 1, "text_item": {"text": "你好"}}
            ],
            "context_token": "..."
        }

    返回值：
        转换成功 → Message
        无文本内容 / 不是用户消息 → None（由调用方决定跳过）
    """
    # 只处理用户发来的消息；机器人自己的消息（回显）直接跳过
    if raw.get("message_type", MESSAGE_TYPE_USER) != MESSAGE_TYPE_USER:
        return None

    # 从 item_list 里提取文本
    content = _extract_text(raw.get("item_list") or [])
    if not content:
        # 第一阶段只支持文本：图片/视频/文件/语音一律先跳过
        return None

    return Message(
        sender_id=raw.get("from_user_id") or "",
        content=content,
        message_id=str(raw.get("message_id") or ""),
        timestamp=int(raw.get("create_time_ms") or 0),
        session_id=raw.get("session_id") or "",
    )


def _extract_text(item_list: list) -> str:
    """从微信 item_list 中提取文本内容。

    规则（对照官方 inbound.js bodyFromItemList）：
    - 找第一个 TEXT 类型的 item，取 text_item.text
    - 语音 item 如果带了服务端转文字结果（voice_item.text），也直接用
    - 其余媒体类型第一阶段忽略
    """
    for item in item_list:
        item_type = item.get("type")

        # 纯文本
        if item_type == MESSAGE_ITEM_TEXT:
            text = (item.get("text_item") or {}).get("text")
            if text is not None:
                return str(text)

        # 语音转文字（微信服务端识别结果）
        if item_type == MESSAGE_ITEM_VOICE:
            text = (item.get("voice_item") or {}).get("text")
            if text:
                return str(text)

    return ""
