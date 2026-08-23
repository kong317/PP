import os

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()


class LLMClient:

    def __init__(self):
        self.client = OpenAI(
            api_key=os.getenv("OPENAI_API_KEY"),
            base_url=os.getenv("OPENAI_BASE_URL"),
        )

        self.model = os.getenv("LLM_MODEL")

    def chat(self, messages):
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
        )

        return response.choices[0].message.content