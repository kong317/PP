# -*- coding: utf-8 -*-
"""
config.py —— 项目全局配置

所有可调参数都集中在这里，从 .env 环境变量读取，带默认值。
不允许把 Token / 地址散落在各个模块里硬编码。

使用方式：
    from config import WECHAT_BASE_URL, setup_logging
"""
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录的 .env 文件（LLM 的 key 也在里面）
load_dotenv()

# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

# 项目根目录（config.py 所在目录的上一级就是项目根……其实就是本目录）
BASE_DIR = Path(__file__).parent

# 数据目录：companion.db（记忆）、wechat/（微信凭证）都放这里
DATA_DIR = BASE_DIR / "data"

# 微信凭证目录：account.json / sync_buf.json / context_tokens.json
# 可用环境变量 WECHAT_DATA_DIR 覆盖（测试时指向临时目录用）
WECHAT_DATA_DIR = (
    Path(os.getenv("WECHAT_DATA_DIR"))
    if os.getenv("WECHAT_DATA_DIR")
    else DATA_DIR / "wechat"
)

# 日志目录：logs/app.log
LOG_DIR = BASE_DIR / "logs"


# ---------------------------------------------------------------------------
# 微信 iLink Bot API 配置
# 参考腾讯官方 openclaw-weixin（https://github.com/Tencent/openclaw-weixin）
# ---------------------------------------------------------------------------

# 腾讯 iLink API 固定域名（扫码登录请求固定走这里）
# 登录成功后服务器会返回 baseurl，后续业务请求优先用 baseurl
WECHAT_BASE_URL = os.getenv("WECHAT_BASE_URL", "https://ilinkai.weixin.qq.com")

# bot_type：官方当前固定为 3（DEFAULT_ILINK_BOT_TYPE）
WECHAT_BOT_TYPE = os.getenv("WECHAT_BOT_TYPE", "3")

# 常规 API 请求超时（秒）：sendMessage / getUploadUrl 等（官方默认 15s）
WECHAT_API_TIMEOUT = float(os.getenv("WECHAT_API_TIMEOUT", "15"))

# 轻量 API 请求超时（秒）：getConfig / sendTyping（官方默认 10s）
WECHAT_CONFIG_TIMEOUT = float(os.getenv("WECHAT_CONFIG_TIMEOUT", "10"))

# getUpdates 长轮询超时（毫秒）：服务器最长 hold 35 秒（官方默认 35000）
WECHAT_POLL_TIMEOUT_MS = int(os.getenv("WECHAT_POLL_TIMEOUT_MS", "35000"))

# 二维码状态轮询单次请求超时（秒）：官方同样用 35 秒长轮询
WECHAT_QR_POLL_TIMEOUT = float(os.getenv("WECHAT_QR_POLL_TIMEOUT", "35"))

# 扫码登录总等待时长（秒）：官方默认 480 秒（8 分钟）
WECHAT_LOGIN_TIMEOUT = float(os.getenv("WECHAT_LOGIN_TIMEOUT", "480"))

# 二维码过期后最多自动刷新次数：官方 MAX_QR_REFRESH_COUNT = 3
WECHAT_QR_REFRESH_MAX = int(os.getenv("WECHAT_QR_REFRESH_MAX", "3"))

# 本客户端的版本号：会编码进 iLink-App-ClientVersion 请求头
# 编码规则（官方 buildClientVersion）：major<<16 | minor<<8 | patch
WECHAT_CLIENT_VERSION = os.getenv("WECHAT_CLIENT_VERSION", "1.0.0")

# bot_agent：每条请求 base_info 里带的自声明标识，类似 HTTP User-Agent
# 官方语法要求形如 "名称/版本"，方便微信后台日志归因
WECHAT_BOT_AGENT = os.getenv("WECHAT_BOT_AGENT", "PPCompanion/1.0.0")

# 是否把用户消息完整内容写进日志（默认 0 = 只记录发送者和长度，保护隐私）
WECHAT_LOG_MESSAGE_CONTENT = os.getenv("WECHAT_LOG_MESSAGE_CONTENT", "0") == "1"

# ---------------------------------------------------------------------------
# 用户身份别名（跨渠道身份统一）
# ---------------------------------------------------------------------------
# 问题：同一个人在不同渠道的 user_id 不同（终端="terminal"，微信="xxx@im.wechat"），
# 记忆按 user_id 隔离会导致"微信里的陪陪不认识终端里的记忆"。
# 解决：Gateway 在调用 Companion 前把渠道 ID 解析成"规范用户 ID"。
#
# WECHAT_OWNER_ALIAS：登录者本人（账号主人 ilink_user_id）映射到哪个规范 ID。
#   默认 "terminal"：微信主人和终端对话者是同一个人，共享记忆和会话。
#   设为空字符串 "" 可禁用（主人也按微信 ID 独立记忆）。
WECHAT_OWNER_ALIAS = os.getenv("WECHAT_OWNER_ALIAS", "terminal")

# USER_ALIASES：额外的显式别名映射（JSON 格式），例如把某个好友也映射到同一身份：
#   USER_ALIASES={"friend_xxx@im.wechat":"terminal"}
# 解析失败时按空映射处理，不影响启动。
def _load_user_aliases() -> dict:
    raw = os.getenv("USER_ALIASES", "")
    if not raw:
        return {}
    try:
        import json

        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


USER_ALIASES = _load_user_aliases()


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

def setup_logging() -> None:
    """初始化日志：同时输出到控制台和 logs/app.log。

    注意：
    - 日志文件用 UTF-8 编码（Windows 下默认 GBK 会写乱中文）
    - 绝对不会记录完整 bot_token（打码逻辑见 wechat/util.py）
    """
    LOG_DIR.mkdir(exist_ok=True)

    # Windows 控制台可能不是 UTF-8，强制转一下，避免中文乱码
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass  # 某些环境不支持 reconfigure，忽略即可

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    # 文件输出（UTF-8）
    file_handler = logging.FileHandler(
        LOG_DIR / "app.log", encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.addHandler(console_handler)
    root.addHandler(file_handler)
