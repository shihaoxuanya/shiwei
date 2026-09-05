import sqlite3

from shiwei_ai.storage.database import Database, MIGRATIONS
from shiwei_ai.worker import WorkerServer


def test_v3_history_survives_incremental_upgrade(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    path = data / "shiwei.db"
    connection = sqlite3.connect(path)
    for version, sql in MIGRATIONS[:3]:
        connection.executescript(sql)
        connection.execute("INSERT INTO schema_migrations VALUES (?, 'old')", (version,))
        connection.commit()
    connection.execute("INSERT INTO conversations VALUES ('old', '旧对话', 'old', 'old')")
    connection.execute("INSERT INTO messages VALUES ('m', 'old', 'assistant', '旧回答保留', 'old', NULL)")
    connection.commit()
    connection.close()
    upgraded = Database(path)
    assert upgraded.connection.execute("SELECT content FROM messages").fetchone()[0] == "旧回答保留"
    upgraded.close()
    server = WorkerServer(data)
    detail = server._get_conversation({"conversationId": "old"})["conversation"]
    assert detail["messages"][0]["content"] == "旧回答保留"
    assert detail["messages"][0]["sourceMatches"] == []
    server._get_importer().close()
