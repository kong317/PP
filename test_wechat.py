# -*- coding: utf-8 -*-
"""
test_wechat.py —— 微信网关的离线单元测试（不需要网络和微信）

覆盖：
    1. Message 模型 + from_weixin 适配器（文本/非文本/机器人消息过滤）
    2. 账号凭证读写（保存 → 加载 → 清除，token 不泄漏到日志）
    3. WeChatClient 请求头构造（Authorization / X-WECHAT-UIN / 版本号）
    4. token 打码工具

运行：python test_wechat.py
"""
import json
import os
import tempfile

# 必须在 import config / wechat.account 之前设置：
# config.py 在 import 时读取环境变量决定凭证目录位置，
# 这样测试就全部落在临时目录里，绝不污染真实凭证（data/wechat/）。
TEST_DATA_DIR = tempfile.mkdtemp(prefix="pp_wechat_test_")
os.environ["WECHAT_DATA_DIR"] = TEST_DATA_DIR

import config  # noqa: E402
from wechat.account import (  # noqa: E402
    WeChatAccount,
    clear_account,
    load_account,
    load_context_tokens,
    load_sync_buf,
    save_account,
    save_context_tokens,
    save_sync_buf,
)
from wechat.client import WeChatClient, build_client_version_uint  # noqa: E402
from wechat.message import from_weixin  # noqa: E402
from wechat.util import redact_json_body, redact_token  # noqa: E402


# ---------------------------------------------------------------------------
# 1. Message 适配器
# ---------------------------------------------------------------------------

def test_from_weixin_text():
    raw = {
        "seq": 1,
        "message_id": "10086",
        "from_user_id": "abc@im.wechat",
        "to_user_id": "bot@im.bot",
        "create_time_ms": 1756348800000,
        "session_id": "s1",
        "message_type": 1,
        "message_state": 2,
        "item_list": [{"type": 1, "text_item": {"text": "你好"}}],
        "context_token": "ct_xxx",
    }
    msg = from_weixin(raw)
    assert msg is not None
    assert msg.sender_id == "abc@im.wechat"
    assert msg.content == "你好"
    assert msg.message_id == "10086"
    assert msg.timestamp == 1756348800000
    assert msg.session_id == "s1"
    print("PASS: test_from_weixin_text")


def test_from_weixin_skips_bot_message():
    # message_type=2 是机器人自己的消息，必须跳过
    raw = {
        "message_id": "1",
        "from_user_id": "bot@im.bot",
        "message_type": 2,
        "item_list": [{"type": 1, "text_item": {"text": "回声"}}],
    }
    assert from_weixin(raw) is None
    print("PASS: test_from_weixin_skips_bot_message")


def test_from_weixin_skips_media():
    # 图片消息（type=2）没有文本，第一阶段应跳过
    raw = {
        "message_id": "2",
        "from_user_id": "abc@im.wechat",
        "message_type": 1,
        "item_list": [{"type": 2, "image_item": {}}],
    }
    assert from_weixin(raw) is None
    print("PASS: test_from_weixin_skips_media")


def test_from_weixin_voice_text():
    # 语音消息带服务端转文字结果时，直接当文本用（官方 bodyFromItemList 规则）
    raw = {
        "message_id": "3",
        "from_user_id": "abc@im.wechat",
        "message_type": 1,
        "item_list": [{"type": 3, "voice_item": {"text": "在干嘛"}}],
    }
    msg = from_weixin(raw)
    assert msg is not None and msg.content == "在干嘛"
    print("PASS: test_from_weixin_voice_text")


# ---------------------------------------------------------------------------
# 2. 账号凭证读写
# ---------------------------------------------------------------------------

def test_account_roundtrip():
    account = WeChatAccount(
        account_id="bot123@im.bot",
        token="super_secret_token_abc",
        base_url="https://ilinkai.weixin.qq.com",
        user_id="user1@im.wechat",
    )
    save_account(account)

    loaded = load_account()
    assert loaded is not None
    assert loaded.account_id == "bot123@im.bot"
    assert loaded.token == "super_secret_token_abc"
    assert loaded.user_id == "user1@im.wechat"

    clear_account()
    assert load_account() is None
    print("PASS: test_account_roundtrip")


def test_account_missing_returns_none():
    assert load_account() is None
    print("PASS: test_account_missing_returns_none")


def test_sync_buf_roundtrip():
    assert load_sync_buf() == ""
    save_sync_buf("buf_12345")
    assert load_sync_buf() == "buf_12345"
    print("PASS: test_sync_buf_roundtrip")


def test_context_tokens_roundtrip():
    assert load_context_tokens() == {}
    save_context_tokens({"u1@im.wechat": "ct_1", "u2@im.wechat": "ct_2"})
    loaded = load_context_tokens()
    assert loaded == {"u1@im.wechat": "ct_1", "u2@im.wechat": "ct_2"}
    print("PASS: test_context_tokens_roundtrip")


def test_bad_json_does_not_crash():
    # 凭证文件损坏时不能抛异常，只能返回 None
    with open(os.path.join(config.WECHAT_DATA_DIR, "account.json"), "w") as f:
        f.write("{broken json")
    assert load_account() is None
    print("PASS: test_bad_json_does_not_crash")


# ---------------------------------------------------------------------------
# 3. WeChatClient 请求头
# ---------------------------------------------------------------------------

def test_build_client_version_uint():
    assert build_client_version_uint("1.0.11") == 0x0001000B  # 官方示例 65547
    assert build_client_version_uint("2.4.6") == 0x00020406
    assert build_client_version_uint("1.0.0") == 0x00010000
    print("PASS: test_build_client_version_uint")


def test_headers():
    client = WeChatClient(base_url="https://example.com", token="tok123")
    headers = client._build_headers()
    assert headers["AuthorizationType"] == "ilink_bot_token"
    assert headers["Authorization"] == "Bearer tok123"
    assert headers["iLink-App-Id"] == "bot"
    # X-WECHAT-UIN 是"十进制字符串的 base64"，两次请求应不同（随机）
    import base64
    uin1 = headers["X-WECHAT-UIN"]
    uin2 = client._build_headers()["X-WECHAT-UIN"]
    assert uin1 != uin2
    # 能解回十进制数字字符串
    decoded = base64.b64decode(uin1).decode("ascii")
    int(decoded)  # 是数字
    assert headers["iLink-App-ClientVersion"] == str(0x00010000)
    print("PASS: test_headers")


# ---------------------------------------------------------------------------
# 4. 用户身份解析（跨渠道身份统一）
# ---------------------------------------------------------------------------

def test_user_alias_resolution():
    import config
    from wechat.gateway import WeChatGateway

    gateway = WeChatGateway(companion=None)
    gateway.account = WeChatAccount(
        account_id="bot1@im.bot",
        token="t",
        user_id="owner@im.wechat",
    )

    # 登录者本人 → 默认映射到 "terminal"（和终端对话共享记忆）
    assert gateway._resolve_user_id("owner@im.wechat") == "terminal"

    # 其他微信用户 → 保持自己的 ID（互相隔离）
    assert gateway._resolve_user_id("friend@im.wechat") == "friend@im.wechat"

    # 显式别名配置优先于默认规则
    config.USER_ALIASES = {"friend@im.wechat": "terminal"}
    assert gateway._resolve_user_id("friend@im.wechat") == "terminal"

    # 禁用主人映射后，主人也按微信 ID 独立
    config.WECHAT_OWNER_ALIAS = ""
    assert gateway._resolve_user_id("owner@im.wechat") == "owner@im.wechat"

    # 恢复默认配置，避免影响后续测试
    config.USER_ALIASES = {}
    config.WECHAT_OWNER_ALIAS = "terminal"
    print("PASS: test_user_alias_resolution")


# ---------------------------------------------------------------------------
# 5. 日志打码
# ---------------------------------------------------------------------------

def test_redact_token():
    assert redact_token("") == "(none)"
    assert redact_token(None) == "(none)"
    assert redact_token("abc") == "****(len=3)"
    assert redact_token("abcdef123456") == "abcdef…(len=12)"
    print("PASS: test_redact_token")


def test_redact_json_body():
    body = json.dumps({
        "ret": 0,
        "bot_token": "SECRET_TOKEN_VALUE",
        "context_token": "CT_SECRET",
        "msgs": [],
    })
    redacted = redact_json_body(body)
    assert "SECRET_TOKEN_VALUE" not in redacted
    assert "CT_SECRET" not in redacted
    assert "<redacted>" in redacted
    print("PASS: test_redact_json_body")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    test_from_weixin_text()
    test_from_weixin_skips_bot_message()
    test_from_weixin_skips_media()
    test_from_weixin_voice_text()
    test_account_roundtrip()
    test_account_missing_returns_none()
    test_sync_buf_roundtrip()
    test_context_tokens_roundtrip()
    test_bad_json_does_not_crash()
    test_build_client_version_uint()
    test_headers()
    test_user_alias_resolution()
    test_redact_token()
    test_redact_json_body()
    print("\n全部测试通过 ✅")
