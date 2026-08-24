class Memory:

    def __init__(self):
        self.memories = []

    def add(self, memory):
        self.memories.append(memory)

    def get_all(self):
        return self.memories
    def get_context(self):
        if not self.memories:
            return ""

        lines = []

        for memory in self.memories:
            lines.append(
                f"- {memory['content']}"
            )

        return "\n".join(lines)#格式化meory给大模型将memory变为易读取的Prompt