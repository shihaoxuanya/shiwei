from __future__ import annotations

import json

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
    assert vector_store.count() == 0  # Saving is local; indexing is a separate request.
    assert updated["retrieval"]["keyword"] == "ready"
    assert updated["retrieval"]["semantic"] == "pending"
    server._index_note({"noteId": note["id"], "revision": updated["updatedAt"]})
    assert vector_store.count() == 1

    answer = server._chat({"query": "黄总之前确认需要几套环境？"})
    assert "四套" in answer["answer"]
    assert answer["citations"][0]["citationId"] == "N1"
    assert answer["citations"][0]["sourceFilename"] == "TiDB会议记录"
    assert answer["citations"][0]["noteId"] == note["id"]

    revised = server._update_note(
        {
            "noteId": note["id"],
            "title": "TiDB会议记录",
            "content": "实际最终确认需要五套环境。",
        }
    )["note"]
    server._index_note({"noteId": note["id"], "revision": revised["updatedAt"]})
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


def test_offline_note_save_search_restart_and_empty_status(tmp_path):
    server = WorkerServer(tmp_path / "data")
    note = server._create_note({})["note"]
    assert note["retrieval"]["keyword"] == "empty"
    saved = server._update_note({"noteId": note["id"], "title": "范围记录", "content": "单次全量迁移38~50天，增量7~14天。"})["note"]
    assert saved["retrieval"] == {"revision": saved["updatedAt"], "keyword": "ready", "semantic": "disabled"}
    assert server._search_lexical({"query": "全量迁移"})["hits"]
    server._get_importer().close()
    restarted = WorkerServer(tmp_path / "data")
    loaded = restarted._get_note({"noteId": note["id"]})["note"]
    assert loaded["content"] == "单次全量迁移38~50天，增量7~14天。"
    assert loaded["retrieval"] == saved["retrieval"]
    restarted._get_importer().close()


def test_embedding_failure_does_not_block_local_note_save(tmp_path):
    server = configured_server(tmp_path)
    calls = []
    def unavailable(texts):
        calls.extend(texts)
        raise RuntimeError("offline")
    server._gateway.embed = unavailable
    note = server._create_note({})["note"]
    saved = server._update_note({"noteId": note["id"], "title": "离线记录", "content": "唯一关键字水杉"})["note"]
    assert calls == []
    assert server._search_lexical({"query": "水杉"})["hits"]
    indexed = server._index_note({"noteId": note["id"], "revision": saved["updatedAt"]})["note"]
    assert indexed["retrieval"]["keyword"] == "ready"
    assert indexed["retrieval"]["semantic"] == "failed"
    assert server._get_note({"noteId": note["id"]})["note"]["content"] == "唯一关键字水杉"
    assert len(calls) == 1
    server._get_importer().close()


def test_stale_and_deleted_note_index_requests_cannot_restore_old_content(tmp_path, monkeypatch):
    monkeypatch.setattr("shiwei_ai.notes.service.utc_now", lambda: "2026-09-08T06:00:00+00:00")
    server = configured_server(tmp_path)
    note = server._create_note({})["note"]
    old = server._update_note({"noteId": note["id"], "title": "项目", "content": "老版本水杉"})["note"]
    new = server._update_note({"noteId": note["id"], "title": "项目", "content": "新版本银杏"})["note"]
    assert new["updatedAt"] > old["updatedAt"] > note["updatedAt"]
    assert server._index_note({"noteId": note["id"], "revision": old["updatedAt"]})["skipped"] == "stale_revision"
    assert server._search_lexical({"query": "水杉"})["hits"] == []
    assert server._search_lexical({"query": "银杏"})["hits"]
    server._index_note({"noteId": note["id"], "revision": new["updatedAt"]})
    server._delete_note({"noteId": note["id"]})
    assert server._index_note({"noteId": note["id"], "revision": new["updatedAt"]}) == {"skipped": "deleted"}
    assert server._search_lexical({"query": "银杏"})["hits"] == []
    assert LanceVectorStore(tmp_path / "data" / "index" / "lancedb").count() == 0
    server._get_importer().close()


def test_note_index_never_silently_rebuilds_other_sources(tmp_path):
    server = configured_server(tmp_path)
    first = server._create_note({})["note"]
    server._update_note({"noteId": first["id"], "title": "不能外发的旧记录", "content": "旧记录水杉"})
    second = server._create_note({})["note"]
    saved = server._update_note({"noteId": second["id"], "title": "新记录", "content": "本次银杏"})["note"]
    calls = []
    embed = server._gateway.embed
    def record(texts):
        calls.extend(texts)
        return embed(texts)
    server._gateway.embed = record
    server._index_note({"noteId": second["id"], "revision": saved["updatedAt"]})
    assert calls and all("水杉" not in text for text in calls)
    assert LanceVectorStore(tmp_path / "data" / "index" / "lancedb").count() == 1
    calls.clear()
    server._provider_public["baseUrl"] = "different-provider"
    result = server._index_note({"noteId": second["id"], "revision": saved["updatedAt"]})["note"]
    assert calls == []
    assert result["retrieval"]["semantic"] == "requires_rebuild"
    server._get_importer().close()


def test_note_jsonl_contract_saves_before_index_and_rejects_missing_revision(tmp_path):
    server = WorkerServer(tmp_path / "data")
    def rpc(method, params):
        return json.loads(server.process_line(json.dumps({"jsonrpc": "2.0", "protocol_version": "1.0", "id": "notes-rpc", "method": method, "params": params})))
    created = rpc("create_note", {})["result"]["note"]
    saved = rpc("update_note", {"noteId": created["id"], "title": "标题", "content": "中文检索范围38~50天"})["result"]["note"]
    assert saved["retrieval"]["keyword"] == "ready"
    assert rpc("index_note", {"noteId": created["id"]})["error"]["code"] == "INVALID_NOTE"
    indexed = rpc("index_note", {"noteId": created["id"], "revision": saved["updatedAt"]})["result"]["note"]
    assert indexed["retrieval"]["semantic"] == "disabled"
    assert rpc("get_note", {"noteId": created["id"]})["result"]["note"]["content"] == "中文检索范围38~50天"
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
