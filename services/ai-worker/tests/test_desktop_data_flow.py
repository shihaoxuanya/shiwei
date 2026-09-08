"""Real Worker/SQLite/FTS/Lance flows with only disposable synthetic documents."""
import json
from pathlib import Path

import httpx
import pytest

from shiwei_ai.models import create_gateway
from shiwei_ai.worker import WorkerServer


def rpc(server, method, params=None):
    response = json.loads(server.process_line(json.dumps({
        "jsonrpc": "2.0", "protocol_version": "1.0", "id": "desktop-data-test",
        "method": method, "params": params or {},
    })))
    assert "error" not in response, response
    return response["result"]


@pytest.fixture
def library(tmp_path):
    server = WorkerServer(tmp_path / "library")
    yield server
    server._shutdown({})


@pytest.fixture
def model_requests(monkeypatch):
    calls = []

    def transport(request):
        body = json.loads(request.content)
        calls.append({"url": str(request.url), "authorization": request.headers.get("authorization"), "body": body})
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"model": body["model"], "data": [{"index": index, "embedding": [1.0, 0.5, 0.25]} for index, _ in enumerate(body["input"])]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "连接测试成功"}}]})

    monkeypatch.setattr("shiwei_ai.worker.server.create_gateway", lambda config: create_gateway(config, transport=httpx.MockTransport(transport)))
    return calls


def config(**changes):
    return {"baseUrl": "https://chat.example.test/v1", "apiKey": "synthetic-chat-key", "chatModel": "chat-a", "embeddingMode": "separate", "embeddingBaseUrl": "https://vector.example.test/v1", "embeddingApiKey": "synthetic-vector-key", "embeddingModel": "embed-a", **changes}


def add_file(server, tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    report = rpc(server, "import_paths", {"paths": [str(path)]})
    assert report["summary"]["imported"] == 1, report
    return path, report["imported"][0]["sourceId"], report


@pytest.mark.parametrize("query", ["迁移", "Oracle", "ORA-00600"])
def test_body_search_returns_true_source_identity_and_real_locators(library, tmp_path, query):
    text = "\n\n".join(f"第{index}节：本节讨论 Oracle 迁移，排查 ORA-00600，保留原始错误码。" * 16 for index in range(8))
    _, source_id, _ = add_file(library, tmp_path, "内部说明.txt", text)
    hits = rpc(library, "search_lexical", {"query": query, "limit": 100})["hits"]
    assert len(hits) > 1, "Fixture must exercise more than one chunk from one file"
    assert {hit["sourceId"] for hit in hits} == {source_id}
    assert {hit["filename"] for hit in hits} == {"内部说明.txt"}
    assert all(query in hit["content"] for hit in hits)
    assert all(not hit.get("pageNumber") for hit in hits), "Plain text must not acquire invented PDF pages"
    assert all(hit["sourceType"] == "imported_file" for hit in hits)
    assert len(rpc(library, "list_sources")["sources"]) == 1


def test_deleting_managed_copy_after_original_moves_preserves_both_user_paths(library, tmp_path):
    original, source_id, _ = add_file(library, tmp_path, "最初位置.txt", "必须保留的原文银杏")
    moved = tmp_path / "移动后的原件.txt"
    original.rename(moved)
    original.write_text("原路径新建的另一个文件", encoding="utf-8")
    stored = Path(rpc(library, "list_sources")["sources"][0]["storedPath"])
    rpc(library, "delete_source", {"sourceId": source_id})
    assert original.read_text(encoding="utf-8") == "原路径新建的另一个文件"
    assert moved.read_text(encoding="utf-8") == "必须保留的原文银杏"
    assert not stored.exists()
    assert rpc(library, "search_lexical", {"query": "银杏"})["hits"] == []


def test_saved_keyword_and_failed_parse_are_not_reported_as_fully_ready(library, tmp_path):
    add_file(library, tmp_path, "离线记录.txt", "不需要 API Key 的本地全文搜索。")
    source = rpc(library, "list_sources")["sources"][0]
    assert source["retrieval"] == {"keyword": "ready", "semantic": "disabled"}
    broken = tmp_path / "损坏.pdf"
    broken.write_bytes(b"not a valid PDF")
    report = rpc(library, "import_paths", {"paths": [str(broken)]})
    assert report["summary"]["failed"] == 1 and report["failed"][0]["reason"]
    failed = next(s for s in rpc(library, "list_sources")["sources"] if s["filename"] == broken.name)
    assert failed["status"] == "failed" and failed["error"]
    assert failed["retrieval"]["keyword"] == "unavailable"


def test_connection_tests_are_separate_and_send_only_synthetic_text(library, tmp_path, model_requests):
    add_file(library, tmp_path, "不得发出的资料.txt", "个人内容标记隐私水杉")
    chat = rpc(library, "provider_test", {**config(), "target": "chat"})
    assert chat["chatOk"] is True
    assert len(model_requests) == 1
    assert model_requests[0]["url"].startswith("https://chat.example.test/")
    assert model_requests[0]["authorization"] == "Bearer synthetic-chat-key"
    model_requests.clear()
    embedding = rpc(library, "provider_test", {**config(), "target": "embedding"})
    assert embedding["embeddingEnabled"] is True and embedding["chatOk"] is False
    assert len(model_requests) == 1
    assert model_requests[0]["url"].startswith("https://vector.example.test/")
    assert model_requests[0]["authorization"] == "Bearer synthetic-vector-key"
    assert model_requests[0]["body"]["input"] == ["拾微连接测试"]
    assert "隐私水杉" not in json.dumps(model_requests, ensure_ascii=False)


def test_model_changes_require_confirmation_before_any_existing_text_resend(library, tmp_path, model_requests):
    rpc(library, "provider_configure", config())
    _, first_id, _ = add_file(library, tmp_path, "第一份.txt", "旧资料紫杉，不能因为更换模型就自动批量重发。")
    before = rpc(library, "index_status")
    assert before["needsRebuild"] is False
    model_requests.clear()
    rpc(library, "provider_configure", config(chatModel="chat-b"))
    assert model_requests == []
    assert rpc(library, "index_status")["needsRebuild"] is False
    rpc(library, "provider_configure", config(chatModel="chat-b", embeddingModel="embed-b"))
    assert model_requests == []
    assert rpc(library, "index_status")["needsRebuild"] is True
    # Reindex and a new import must not start a model-change rebuild by accident.
    reindex = rpc(library, "reindex_source", {"sourceId": first_id})
    assert reindex["embeddingIndex"]["requiresRebuild"] is True
    _, _, report = add_file(library, tmp_path, "第二份.txt", "新资料红杉，仍能先用关键词找到。")
    assert report["embeddingIndex"]["requiresRebuild"] is True
    assert model_requests == [], "Preflight must block BEFORE the first embed batch"
    assert rpc(library, "index_status")["embeddingVersionId"] == before["embeddingVersionId"]
    assert all(s["retrieval"]["keyword"] == "ready" for s in rpc(library, "list_sources")["sources"])
    rpc(library, "rebuild_embeddings")  # The existing explicit confirmation action.
    assert model_requests and all(c["body"]["model"] == "embed-b" for c in model_requests)
    assert "旧资料紫杉" in json.dumps(model_requests, ensure_ascii=False)
    assert rpc(library, "index_status")["needsRebuild"] is False


def test_regular_import_and_delete_never_reupload_unrelated_sources(library, tmp_path, model_requests):
    rpc(library, "provider_configure", config())
    _, first_id, _ = add_file(library, tmp_path, "第一份.txt", "旧资料独有关键字水杉")
    model_requests.clear()
    _, second_id, _ = add_file(library, tmp_path, "第二份.txt", "新资料独有关键字银杏")
    payloads = json.dumps(model_requests, ensure_ascii=False)
    assert "银杏" in payloads and "水杉" not in payloads
    model_requests.clear()
    rpc(library, "delete_source", {"sourceId": second_id})
    assert model_requests == []
    assert [s["id"] for s in rpc(library, "list_sources")["sources"]] == [first_id]
    assert rpc(library, "search_lexical", {"query": "银杏"})["hits"] == []


def test_library_file_scope_is_applied_before_note_candidate_limits(library, tmp_path):
    _, source_id, _ = add_file(library, tmp_path, "普通说明.txt", "Oracle 迁移的正文可检索。")
    for index in range(105):
        note = rpc(library, "create_note")["note"]
        rpc(library, "update_note", {"noteId": note["id"], "title": f"Oracle 迁移摘录 {index}", "content": "Oracle 迁移仅为合成笔记，不能淹没资料页的文件。"})
    # Without a scope, metadata-ranked notes fill this bounded candidate list.
    ordinary = rpc(library, "search_lexical", {"query": "迁移", "limit": 100})["hits"]
    assert source_id not in {h["sourceId"] for h in ordinary}
    files = rpc(library, "search_lexical", {"query": "迁移", "limit": 100, "sourceType": "imported_file"})["hits"]
    assert {h["sourceId"] for h in files} == {source_id}
    assert files[0]["content"] == "Oracle 迁移的正文可检索。"
    assert all(h["sourceType"] == "imported_file" for h in files)
    assert any("chunks_fts" in h["matchedBy"] for h in files), "Scoped body search must actually use FTS"


def test_file_scope_also_applies_to_short_word_body_fallback(library, tmp_path):
    _, source_id, _ = add_file(library, tmp_path, "合成说明.txt", "正文包含水杉，来源是文件。")
    note = rpc(library, "create_note")["note"]
    rpc(library, "update_note", {"noteId": note["id"], "title": "另一条笔记", "content": "正文包含水杉，来源是笔记。"})
    # Simulate a legacy/missing derived FTS projection; originals stay intact.
    with library._get_importer().database.transaction() as connection:
        connection.execute("DELETE FROM chunks_fts")
        connection.execute("DELETE FROM chunks_fts_trigram")
    hits = rpc(library, "search_lexical", {"query": "水杉", "sourceType": "imported_file", "limit": 1})["hits"]
    assert len(hits) == 1 and hits[0]["sourceId"] == source_id
    assert hits[0]["matchedBy"] == ["bounded_substring"]


def test_index_status_keeps_explicit_rebuild_available_until_all_chunks_covered(library, tmp_path, model_requests):
    _, first_id, _ = add_file(library, tmp_path, "旧资料甲.txt", "旧资料甲只有关键词索引。")
    _, second_id, _ = add_file(library, tmp_path, "旧资料乙.txt", "旧资料乙只有关键词索引。")
    rpc(library, "provider_configure", config())
    # The normal per-document path initializes only one document, not all older
    # content. Merely matching model/version must not hide the remaining work.
    library._index_source_vectors(first_id)
    version = rpc(library, "index_status")["embeddingVersionId"]
    model_requests.clear()
    partial = rpc(library, "index_status")
    assert partial["needsRebuild"] is True
    assert partial["chunkCount"] == 2
    assert model_requests == [], "Checking coverage must never send source text"
    assert rpc(library, "index_status")["embeddingVersionId"] == version
    rpc(library, "provider_configure", config(embeddingMode="none"))
    assert rpc(library, "index_status")["needsRebuild"] is False
    rpc(library, "provider_configure", config())
    assert rpc(library, "index_status")["needsRebuild"] is True
    library._index_source_vectors(second_id)
    model_requests.clear()
    complete = rpc(library, "index_status")
    assert complete["needsRebuild"] is False
    assert complete["embeddingVersionId"] == version
    assert model_requests == []
