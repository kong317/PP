import json


class MemoryExtractor:

    def __init__(self, llm):
        self.llm = llm

    def extract(self, user_message):

        prompt = f"""
你是一个 AI Companion 的记忆提取器。

你的任务是判断用户刚才说的话中，
是否存在值得长期记住的信息。

值得记住的信息包括：

1. fact：用户的客观事实
2. preference：用户的偏好
3. experience：用户的重要经历
4. goal：用户的重要目标

不值得记住的内容包括：

- 一次性的日常琐事
- 没有长期价值的信息
- 普通闲聊
- 天气、时间等临时信息

用户刚才说：

{user_message}

请严格返回 JSON：

{{
    "should_remember": true,
    "memories": [
        {{
            "content": "需要记住的内容",
            "type": "fact",
            "importance": 0.8
        }}
    ]
}}

如果没有值得记住的信息：

{{
    "should_remember": false,
    "memories": []
}}

不要输出任何 JSON 以外的内容。
"""

        response = self.llm.chat([
            {
                "role": "system",
                "content": prompt
            }
        ])

        return json.loads(response)