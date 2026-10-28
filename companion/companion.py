# -*- coding: utf-8 -*-
"""
companion.py —— Companion 核心入口类

把原来散落在 main.py 里的完整对话链路封装成一个统一接口：

    class Companion:
        def chat(self, user_id: str, message: str) -> str

内部负责（原 main.py 的完整流程）：
    用户消息
        ↓
    Conversation.add_user_message          （每个 user_id 独立会话）
        ↓
    MemoryRetriever.retrieve               （检索该用户的长期记忆）
        ↓
    构建 system_prompt（携带记忆上下文）
        ↓
    LLMClient.chat                         （生成回答）
        ↓
    MemoryExtractor.extract                （从对话中提取新记忆）
        ↓
    Memory.add（带 user_id 持久化到 SQLite）
        ↓
    返回回答

这样终端和微信（以及以后的 Telegram / Web / QQ）都只调用这一个接口，
Companion 完全不知道消息来自哪个渠道。
"""
import json
import logging

from companion.conversation import Conversation
from companion.database import Database
from companion.llm import LLMClient
from companion.memory import Memory
from companion.memory_extractor import MemoryExtractor
from companion.memory_retriever import MemoryRetriever

logger = logging.getLogger(__name__)


def build_system_prompt(memory_context: str) -> str:
    """构建带记忆上下文的 system prompt（从原 main.py 迁移过来）。"""
    return f"""
你是我的 AI Companion。

你的名字叫陪陪。

你的性格温柔、自然、稍微有一点调皮。

你不会机械地回答问题。
你会认真理解用户说的话。
以下是你对用户的长期记忆：

{memory_context}

请合理利用这些记忆帮助你理解用户。

不要主动向用户展示“记忆系统”的存在。
不要说“根据我的记忆”之类的话。
如果记忆与当前话题无关，不要强行提起。
"""


class Companion:
    """AI Companion 主入口。

    只暴露一个方法：
        chat(user_id, message) -> str

    user_id 是必填的：不同用户拥有完全独立的 Conversation 和 Memory，
    这是从"玩具"走向真正 Companion 的关键设计。
    """

    def __init__(self, llm: LLMClient | None = None, database: Database | None = None):
        # LLM 客户端（可注入，方便测试）
        self.llm = llm or LLMClient()

        # 数据库（记忆持久化）
        self.database = database or Database()

        # 记忆三件套：存储 / 检索 / 提取
        self.memory = Memory(self.database)
        self.memory_retriever = MemoryRetriever(self.llm)
        self.memory_extractor = MemoryExtractor(self.llm)

        # 每个用户一个独立会话：{user_id: Conversation}
        self._conversations: dict[str, Conversation] = {}

    # ------------------------------------------------------------------
    # 对外唯一接口
    # ------------------------------------------------------------------

    def chat(self, user_id: str, message: str) -> str:
        """处理一条用户消息，返回 AI 回答。

        内部流程（对应原 main.py 的 while 循环体，原样保留业务逻辑）：
            ① 记忆检索 → ② 组装 system prompt → ③ LLM 回答 → ④ 提取新记忆

        参数：
            user_id：发送者唯一 ID（微信 xxx@im.wechat / 终端 "terminal"）
            message：用户消息文本
        """
        conversation = self._get_conversation(user_id)

        # ① 记录用户消息到会话历史
        conversation.add_user_message(message)

        # ② 检索该用户的已有记忆（注意：只检索这个用户的，不做跨用户共享）
        relevant_memories = self.memory_retriever.retrieve(
            message,
            self.memory.get_all(user_id),
        )
        logger.debug(f"[{user_id}] 相关记忆: {relevant_memories}")

        # ③ 用记忆上下文生成回答
        memory_context = "\n".join(
            f"- {item['content']}" for item in relevant_memories
        )
        conversation.update_system_prompt(build_system_prompt(memory_context))

        response = self.llm.chat(conversation.get_messages())
        conversation.add_assistant_message(response)

        # ④ 回答完成后，从用户消息中提取新记忆（失败不影响回复，只记日志）
        try:
            extracted = self.memory_extractor.extract(message)
            logger.debug(f"[{user_id}] 记忆分析: {extracted}")

            if extracted.get("should_remember"):
                for item in extracted.get("memories", []):
                    self.memory.add(item, user_id=user_id)
                    logger.debug(f"[{user_id}] 记住: {item}")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            # LLM 可能返回非法 JSON，这里兜底，避免记忆提取失败拖垮整条回复
            logger.warning(f"[{user_id}] Memory extraction failed (ignored): {e}")

        return response

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _get_conversation(self, user_id: str) -> Conversation:
        """获取（或创建）指定用户的会话。

        每个用户一份独立的对话历史，互不干扰。
        """
        if user_id not in self._conversations:
            self._conversations[user_id] = Conversation(system_prompt="")
        return self._conversations[user_id]
