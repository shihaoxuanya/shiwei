from __future__ import annotations

from shiwei_ai.models import EmbeddingResult, ModelGateway
from shiwei_ai.retrieval import LanceVectorStore
from shiwei_ai.worker import WorkerServer


class NoteGateway(ModelGateway):
    def chat(self, messages, **options):  # type: ignore[no-untyped-def]
        return "黄总最终确认需要四套环境。[N1]"

    def stream_chat(self, messages, **options):  # type: ignore[no-untyped-def]
        yield self.chat(messages, **options)

    def embed(self, texts):  # type: ignore[no-untyped-def]
        return EmbeddingResult(
            vectors=[
                [
                    1.0 if "四套" in text else 0.0,
                    1.0 if "五套" in text else 0.0,
                    0.25,
                ]
                for text in texts
            ],
            model="note-test",
            dimension=3,
        )


def configured_server(tmp_path):  # type: ignore[no-untyped-def]
    server = WorkerServer(tmp_path / "data")
    server._gateway = NoteGateway()
    server._provider_public = {
        "baseUrl": "note-test",
        "chatModel": "note-test",
        "embeddingModel": "note-test",
        "embeddingMode": "same",
    }
    return server


def test_create_update_search_reindex_cite_and_delete_note(tmp_path):  # type: ignore[no-untyped-def]
    server = configured_server(tmp_path)
    note = server._create_note({})["note"]
    assert note["title"] == ""
    assert server._list_notes({})["notes"][0]["id"] == note["id"]

    updated = server._update_note(
        {
            "noteId": note["id"],
            "title": "TiDB会议记录",
            "content": "黄总最终确认不同阶段一共需要四套环境，而不是五套。",
        }
    )["note"]
    assert updated["chunkCount"] == 1
    hits = server._search_lexical({"query": "黄总 四套环境"})["hits"]
    assert hits[0]["sourceType"] == "user_note"
    assert hits[0]["noteId"] == note["id"]
    vector_store = LanceVectorStore(tmp_path / "data" / "index" / "lancedb")
    assert vector_store.count() == 1

    answer = server._chat({"query": "黄总之前确认需要几套环境？"})
    assert "四套" in answer["answer"]
    assert answer["citations"][0]["citationId"] == "N1"
    assert answer["citations"][0]["sourceFilename"] == "TiDB会议记录"
    assert answer["citations"][0]["noteId"] == note["id"]

    server._update_note(
        {
            "noteId": note["id"],
            "title": "TiDB会议记录",
            "content": "实际最终确认需要五套环境。",
        }
    )
    assert all(
        "四套" not in hit["content"]
        for hit in server._search_lexical({"query": "四套环境"})["hits"]
    )
    assert server._search_lexical({"query": "五套环境"})["hits"]
    assert vector_store.count() == 1

    server._delete_note({"noteId": note["id"]})
    assert server._list_notes({})["notes"] == []
    assert server._search_lexical({"query": "五套环境"})["hits"] == []
    assert vector_store.count() == 0
    missing = server._chat({"query": "黄总之前确认需要几套环境？"})
    assert missing["answerKind"] == "not_found"
    assert missing["citations"] == []
    server._get_importer().close()


def test_note_search_is_literal_and_title_is_optional(tmp_path):  # type: ignore[no-untyped-def]
    server = WorkerServer(tmp_path / "data")
    note = server._create_note({})["note"]
    saved = server._update_note(
        {"noteId": note["id"], "title": "", "content": "第一行就是临时标题\n正文有 50%_\\ 标记"}
    )["note"]
    assert saved["displayTitle"] == "第一行就是临时标题"
    assert server._list_notes({"query": "%_\\"})["notes"][0]["id"] == note["id"]
    server._get_importer().close()
