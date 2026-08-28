import sqlite3
from pathlib import Path
from datetime import datetime

class Database:

    def __init__(self):

        # 项目根目录
        base_dir = Path(__file__).parent.parent

        # data目录
        data_dir = base_dir / "data"
        data_dir.mkdir(exist_ok=True)

        # 数据库文件
        self.db_path = data_dir / "companion.db"

        self._create_tables()

    def get_connection(self):
        return sqlite3.connect(self.db_path)

    def insert_memory(self, memory, user_id=""):
        connection = self.get_connection()

        cursor = connection.cursor()

        now = datetime.now()

        cursor.execute("""
        INSERT INTO memory
        (content, type, importance, status, user_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            memory["content"],
            memory["type"],
            memory["importance"],
            "active",
            user_id,
            now,
            now
        ))

        connection.commit()

        connection.close()

    def get_memories(self, user_id=""):
        connection = self.get_connection()

        cursor = connection.cursor()

        cursor.execute("""
        SELECT id, content, type, importance, status,
               created_at, updated_at
        FROM memory
        WHERE status = 'active' AND user_id = ?
        ORDER BY id
        """, (user_id,))

        rows = cursor.fetchall()

        connection.close()

        memories = []

        for row in rows:
            memories.append({
                "id": row[0],
                "content": row[1],
                "type": row[2],
                "importance": row[3],
                "status": row[4],
                "created_at": row[5],
                "updated_at": row[6]
            })

        return memories
    def _create_tables(self):

        connection = self.get_connection()

        cursor = connection.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            content TEXT NOT NULL,
            type TEXT NOT NULL,
            importance REAL NOT NULL DEFAULT 0.5,
            status TEXT NOT NULL DEFAULT 'active',
            user_id TEXT NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
        """)

        # 老版本的表没有 user_id 列：自动补列（老数据归给终端用户）
        cursor.execute("PRAGMA table_info(memory)")
        columns = [row[1] for row in cursor.fetchall()]
        if "user_id" not in columns:
            cursor.execute("ALTER TABLE memory ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
        cursor.execute("UPDATE memory SET user_id = 'terminal' WHERE user_id = ''")

        connection.commit()
        connection.close()