import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

# LLM Client对话类
class LLMClient:

    def __init__(self):
        # timeout：防止 LLM 请求无限挂起（微信网关要求不能永久卡死）
        # 默认 120 秒，可在 .env 里用 LLM_TIMEOUT 覆盖
        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
            timeout=float(os.getenv("LLM_TIMEOUT", "120")),
        )
        # LLM模型
        self.model = os.getenv("LLM_MODEL")
        # 对话方法
    def chat(self, messages):
    # 创建对话
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
        )

        return response.choices[0].message.content