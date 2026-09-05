import pytest

from shiwei_ai.worker import WorkerServer
from shiwei_ai.worker.server import WorkerMethodError


@pytest.fixture
def history(tmp_path):
    server = WorkerServer(tmp_path / "data")
    db = server._get_importer().database.connection
    for n in range(115):
        identifier = f"chat-{n:03}"
        date = f"2026-09-04T00:{n // 60:02}:{n % 60:02}Z"
        db.execute("INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)", (identifier, f"对话 {n}", date, date))
        db.execute("INSERT INTO messages (id, conversation_id, role, content, created_at) VALUES (?, ?, 'assistant', ?, ?)", (f"message-{n}", identifier, "磁盘容量 50%_\\ 剩余" if n == 0 else f"合成回复 {n}", date))
    db.commit()
    yield server
    server._get_importer().close()


def test_history_can_page_past_old_100_limit(history):
    pages = [history._list_conversations({"offset": n})["conversations"] for n in (0, 50, 100)]
    assert [len(page) for page in pages] == [50, 50, 15]
    assert len({row["id"] for page in pages for row in page}) == 115
    assert pages[0][0]["id"] == "chat-114"
    assert pages[-1][-1]["preview"] == "磁盘容量 50%_\\ 剩余"


def test_searches_old_messages_as_well_as_titles(history):
    assert history._list_conversations({"query": "磁盘"})["conversations"][0]["id"] == "chat-000"
    assert len(history._list_conversations({"query": "对话 114"})["conversations"]) == 1


def test_literal_wildcards_and_sql_are_not_executed(history):
    assert len(history._list_conversations({"query": "%_\\"})["conversations"]) == 1
    assert history._list_conversations({"query": "' OR 1=1 --"})["conversations"] == []
    assert len(history._list_conversations({})["conversations"]) == 50


@pytest.mark.parametrize("params", [{"offset": -1}, {"offset": True}, {"offset": "0"}, {"offset": 100001}, {"query": ["hi"]}, {"query": "文" * 201}])
def test_history_rejects_invalid_parameters(history, params):
    with pytest.raises(WorkerMethodError):
        history._list_conversations(params)
