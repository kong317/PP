# -*- coding: utf-8 -*-
"""
wechat —— 微信网关包

模块职责划分：
    message.py  项目内部统一消息模型（消息标准化：微信协议 → Message 适配）
    account.py  账号凭证 / 消息游标 / context_token 的本地持久化
    auth.py     扫码登录（二维码获取、状态轮询、全状态处理）
    client.py   腾讯 iLink Bot API 通信（纯 HTTP + JSON）
    gateway.py  网关主循环（登录 → 长轮询 → Companion → 回复 → 异常恢复）
    util.py     日志安全工具（token 打码）
"""
from wechat.gateway import WeChatGateway
from wechat.message import Message, from_weixin

__all__ = ["WeChatGateway", "Message", "from_weixin"]
