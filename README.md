# PP — AI Companion（陪陪）

一个带长期记忆的 AI Companion。微信是它的第一个通信渠道。

## 架构

```
 终端 ──┐
        ├──> Companion.chat(user_id, message) ──> Conversation / Memory / LLM
 微信 ──┘        （每个 user_id 独立会话与记忆，SQLite 持久化）
        ▲
        │
 WeChatGateway（wechat/ 包）
        │
 腾讯 iLink Bot API（官方协议，参考 Tencent/openclaw-weixin）
```

- `companion/`：Companion 核心（LLM / Conversation / Memory / 检索 / 提取 / SQLite）
- `wechat/`：微信网关（登录 / 通信 / 消息标准化 / 主循环）
- `config.py`：全局配置（.env + 默认值）
- `logs/app.log`：运行日志（token 自动打码）

## 快速开始

```bash
# 1. 配置 .env（LLM）
#    OPENAI_API_KEY=...
#    OPENAI_BASE_URL=...
#    LLM_MODEL=...

# 2. 微信模式（默认，直接运行即可；第一次运行会显示二维码，手机微信扫码）
python main.py

# 3. 终端对话模式
python main.py terminal

# 4. 向登录者本人发一条测试消息（验证 sendMessage）
python main.py --test-send
```

微信模式下：启动 → 有凭证直接用（无需重复扫码）→ 长轮询收消息 → Companion 回复 → 微信收到回复。token 失效（errcode=-14）会自动清理凭证并弹出新二维码。

## 配置（.env 可选覆盖）

| 变量 | 默认值 | 说明 |
|---|---|---|
| WECHAT_BASE_URL | https://ilinkai.weixin.qq.com | iLink API 域名 |
| WECHAT_BOT_TYPE | 3 | 官方固定 |
| WECHAT_POLL_TIMEOUT_MS | 35000 | getUpdates 长轮询超时 |
| WECHAT_LOGIN_TIMEOUT | 480 | 扫码等待秒数 |
| WECHAT_QR_REFRESH_MAX | 3 | 二维码过期自动刷新次数 |
| WECHAT_LOG_MESSAGE_CONTENT | 0 | 设为 1 时日志记录完整消息内容（默认只记长度） |
| LLM_TIMEOUT | 120 | LLM 请求超时秒数 |

## 数据与凭证

- `data/companion.db`：记忆（SQLite，按 user_id 隔离）
- `data/wechat/account.json`：登录凭证（bot_token 等，**已 gitignore，绝不提交**）
- `data/wechat/sync_buf.json`：getUpdates 游标（重启续传，不重复收消息）
- `data/wechat/context_tokens.json`：回复消息所需的 context_token 缓存

## 跨渠道身份统一

同一个"你"在不同渠道的 ID 不同（终端=`terminal`，微信=`xxx@im.wechat`）。
Gateway 会在调用 Companion 前解析身份：

- **登录者本人**（账号主人）默认映射到 `terminal` —— 微信里聊天能"记得"终端时代的记忆，反之亦然
- 其他微信用户保持独立身份，记忆互不共享
- `.env` 可覆盖：
  - `WECHAT_OWNER_ALIAS=terminal`（默认；设为空字符串则主人也按微信 ID 独立记忆）
  - `USER_ALIASES={"friend@im.wechat":"terminal"}`（把某个好友显式映射到同一身份）

## 测试

```bash
python test_wechat.py     # 离线单测（消息适配 / 凭证读写 / 请求头 / 打码）
python test_database.py   # 数据库单测
```

微信联调测试（需真人扫码）：
1. 首次启动 → 终端出现二维码
2. 扫码 → 登录成功 → 凭证落盘
3. Ctrl+C 退出 → 重启 → 不再要求扫码
4. 微信发"你好" → 收到回复
5. `python main.py wechat --test-send` → 微信收到测试消息
