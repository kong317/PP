# 这是一个示例 Python 脚本。

# 按 Shift+F10 执行或将其替换为您的代码。
# 按 双击 Shift 在所有地方搜索类、文件、工具窗口、操作和设置。


# def print_hi(name):
#     # 在下面的代码行中使用断点来调试脚本。
#     print(f'Hi, {name}')  # 按 Ctrl+F8 切换断点。


# # 按装订区域中的绿色按钮以运行脚本。
# #if __name__ == '__main__':
#     print_hi('HELLO,WORLD')

# 访问 https://www.jetbrains.com/help/pycharm/ 获取 PyCharm 帮助

from companion.llm import LLMClient
from companion.conversation import Conversation


SYSTEM_PROMPT = """
你是我的 AI Companion。

你的名字叫陪陪。

你的性格温柔、自然、稍微有一点调皮。

你不会机械地回答问题。
你会认真理解用户说的话。
"""


llm = LLMClient()

conversation = Conversation(
    system_prompt=SYSTEM_PROMPT
)


while True:

    user_input = input("你：")

    if user_input == "exit":
        break

    conversation.add_user_message(user_input)

    response = llm.chat(
        conversation.get_messages()
    )

    print("陪陪：", response)

    conversation.add_assistant_message(response)
