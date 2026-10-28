# -*- coding: utf-8 -*-
"""
util.py —— 微信模块通用小工具

主要是日志安全相关的打码函数：
    绝对不允许把完整 bot_token / context_token 写进日志。
"""
import re


# 只展示 token 前 6 个字符 + 总长度（对照官方 util/redact.js 的做法）
TOKEN_PREFIX_LEN = 6

# 日志里需要打码的敏感 JSON 字段名
SENSITIVE_FIELDS = (
    "bot_token", "token", "context_token", "authorization", "Authorization",
)


def redact_token(token) -> str:
    """把 token 打码：'abcdef123456...' -> 'abcdef…(len=24)'。

    短 token 直接显示 ****，不暴露任何字符。
    """
    if not token:
        return "(none)"
    token = str(token)
    if len(token) <= TOKEN_PREFIX_LEN:
        return f"****(len={len(token)})"
    return f"{token[:TOKEN_PREFIX_LEN]}…(len={len(token)})"


def redact_json_body(body: str, max_len: int = 200) -> str:
    """把 JSON 字符串里的敏感字段值替换成 <redacted> 后再截断。

    用法：logger.debug(redact_json_body(json.dumps(resp)))
    """
    if not body:
        return "(empty)"

    # 用回调把匹配到的 "key":"value" 替换成 "key":"<redacted>"
    redacted = re.sub(
        r'("(?:' + "|".join(SENSITIVE_FIELDS) + r')"\s*:\s*")[^"]*(")',
        r'\1<redacted>\2',
        body,
    )

    if len(redacted) <= max_len:
        return redacted
    return f"{redacted[:max_len]}…(truncated, totalLen={len(redacted)})"
