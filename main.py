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
from companion.memory import Memory
from companion.memory_extractor import MemoryExtractor
from companion.memory_retriever import MemoryRetriever

def build_system_prompt(memory_context):
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


# 1. 创建 LLM
llm = LLMClient()

# 2. 创建 Conversation
conversation = Conversation(
    system_prompt=""
)

# 3. 创建 Memory
memory = Memory()

# 4. 创建 MemoryExtractor，并把 LLM 注入进去
memory_extractor = MemoryExtractor(llm)
memory_retriever = MemoryRetriever(llm)


while True:

    user_input = input("你：")

    if user_input == "exit":
        break

    # 当前阶段先正常聊天
    conversation.add_user_message(user_input)
    # Memory Extractor
    conversation.add_user_message(user_input)

    # ① 先检索“已有记忆”
    relevant_memories = memory_retriever.retrieve(
        user_input,
        memory.get_all()
    )

    print("相关记忆：", relevant_memories)

    # ② 使用已有记忆生成回答
    memory_context = "\n".join(
        f"- {item['content']}"
        for item in relevant_memories
    )

    system_prompt = build_system_prompt(
        memory_context
    )

    conversation.update_system_prompt(
        system_prompt
    )

    response = llm.chat(
        conversation.get_messages()
    )

    print("陪陪：", response)

    conversation.add_assistant_message(response)

    # ③ 回答完成以后，再提取“新的记忆”
    extracted = memory_extractor.extract(user_input)

    print("记忆分析：", extracted)

    if extracted["should_remember"]:

        for item in extracted["memories"]:
            memory.add(item)

            print("记住：", item)