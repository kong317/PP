class Conversation:

    def __init__(self, system_prompt):
        self.messages = [
            {
                "role": "system",
                "content": system_prompt
            }
        ]

    def add_user_message(self, content):
        self.messages.append({
            "role": "user",
            "content": content
        })

    def add_assistant_message(self, content):
        self.messages.append({
            "role": "assistant",
            "content": content
        })

    def get_messages(self):
        return self.messages
    def update_system_prompt(self, system_prompt):
        self.messages[0] = {
            "role": "system",
            "content": system_prompt
        }#每次对话要重新携带记忆然后更新我们的system_prompt