from companion.database import Database


db = Database()

memory = {
    "content": "用户喜欢 Justin Bieber",
    "type": "preference",
    "importance": 0.7
}

db.insert_memory(memory)

print("Memory保存成功！")