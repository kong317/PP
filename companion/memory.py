from companion.database import Database


class Memory:

    def __init__(self,database):
        self.database = database# 数据库

    def add(self, memory, user_id=""):
        # 添加记忆（带 user_id，每个用户的记忆互相隔离）
        self.database.insert_memory(memory, user_id)

    def get_all(self, user_id=""):
        # 只获取指定用户的记忆
        return self.database.get_memories(user_id)
