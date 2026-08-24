#记忆检索功能初实现
import json


class MemoryRetriever:

    def __init__(self, llm):
        self.llm = llm

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