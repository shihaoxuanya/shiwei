from __future__ import annotations

from pathlib import Path

import pytest

from shiwei_ai.worker import WorkerServer
from shiwei_ai.worker.server import WorkerMethodError


def test_delete_conversation_cascades_chat_only(tmp_path: Path) -> None:
    server = WorkerServer(tmp_path / "data")
    source = tmp_path / "record.txt"
    source.write_text("保留的外部资料", encoding="utf-8")
    server._get_importer().import_paths([str(source)])
    note = server._create_note({})["note"]
    server._update_note({"noteId": note["id"], "title": "保留的笔记", "content": "笔记正文"})
    db = server._get_importer().database.connection
    chunk = db.execute(
        """
        SELECT c.id, c.document_id, c.content, s.original_filename
        FROM chunks c JOIN documents d ON d.id=c.document_id
        JOIN sources s ON s.id=d.source_id WHERE s.source_type='imported_file' LIMIT 1
        """
    ).fetchone()
    conversation_id = server._save_exchange(
        None,
        "问题",
        "回答 [S1]",
        [
            {
                "citationId": "S1",
                "documentId": chunk["document_id"],
                "chunkId": chunk["id"],
                "sourceFilename": chunk["original_filename"],
                "snippet": chunk["content"],
            }
        ],
    )
    source_count = db.execute("SELECT count(*) FROM sources").fetchone()[0]
    note_count = db.execute("SELECT count(*) FROM notes").fetchone()[0]

    result = server._delete_conversation({"conversationId": conversation_id})

    assert result["deleted"] is True
    assert result["messagesDeleted"] == 2
    assert result["citationsDeleted"] == 1
    assert db.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM citations").fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM sources").fetchone()[0] == source_count
    assert db.execute("SELECT count(*) FROM notes").fetchone()[0] == note_count
    server._get_importer().close()


def test_delete_missing_conversation_is_reported(tmp_path: Path) -> None:
    server = WorkerServer(tmp_path / "data")
    with pytest.raises(WorkerMethodError, match="没有找到"):
        server._delete_conversation({"conversationId": "missing"})
    server._get_importer().close()
