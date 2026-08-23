class Memory:

    def __init__(self):
        self.memories = []

    def add(self, memory):
        self.memories.append(memory)

    def get_all(self):
        return self.memories