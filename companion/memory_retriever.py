#记忆检索功能初实现
import json

# 记忆检索器类
class MemoryRetriever:
    # 初始化方法，接受一个LLM客户端作为参数
    def __init__(self, llm):
        self.llm = llm

    # 检索方法，接受用户消息和记忆列表作为参数
    def retrieve(self, user_message, memories):

        if not memories:
            return []

        memories_text = "\n".join(
            f"{i}: {memory['content']}"
            for i, memory in enumerate(memories)
        )

        prompt = f"""
你是 AI Companion 的记忆检索器。

当前用户说：

{user_message}

以下是用户已有的长期记忆：

{memories_text}

请判断哪些记忆与当前用户的话有关。

只返回相关记忆的编号。

例如：

[0, 2]

如果没有任何相关记忆：

[]

不要输出其他内容。
"""

        response = self.llm.chat([
            {
                "role": "system",
                "content": prompt
            }
        ])

        indexes = json.loads(response)

        return [
            memories[index]
            for index in indexes
            if 0 <= index < len(memories)
        ]