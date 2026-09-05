from __future__ import annotations

import json

from shiwei_ai.worker import WorkerServer
from shiwei_ai.models import EmbeddingResult, ModelGateway


class StreamingGateway(ModelGateway):
    def chat(self, messages, **options):  # type: ignore[no-untyped-def]
        return "回答。[S1]"

    def stream_chat(self, messages, **options):  # type: ignore[no-untyped-def]
        yield "回答"
        yield "。[S1]"

    def embed(self, texts):  # type: ignore[no-untyped-def]
        return EmbeddingResult(vectors=[[1.0, 0.0] for _ in texts], model="fake", dimension=2)


def request(method: str, request_id: str = "test-1", params: dict | None = None) -> str:
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "protocol_version": "1.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        }
    )


def test_ping_returns_versioned_pong() -> None:
    response = json.loads(WorkerServer().process_line(request("ping")))

    assert response["id"] == "test-1"
    assert response["result"]["status"] == "pong"
    assert response["result"]["protocolVersion"] == "1.0"


def test_malformed_json_is_a_protocol_error() -> None:
    response = json.loads(WorkerServer().process_line("not-json"))

    assert response["id"] is None
    assert response["error"]["code"] == "PARSE_ERROR"


def test_unknown_method_does_not_crash_worker() -> None:
    response = json.loads(WorkerServer().process_line(request("missing")))

    assert response["error"]["code"] == "METHOD_NOT_FOUND"


def test_shutdown_marks_server_for_exit() -> None:
    server = WorkerServer()
    response = json.loads(server.process_line(request("shutdown")))

    assert response["result"]["status"] == "stopping"
    assert server.should_stop is True


def test_switching_embedding_endpoint_or_model_never_mixes_old_vectors(tmp_path):
    server = WorkerServer(tmp_path / "data")
    gateway = StreamingGateway()
    server._gateway = gateway
    server._provider_public = {"baseUrl": "https://chat.example/v1", "chatModel": "chat-a", "embeddingMode": "separate", "embeddingBaseUrl": "https://vectors.example/v1", "embeddingModel": "fake"}
    database = server._get_importer().database
    with database.transaction() as connection:
        connection.execute("INSERT INTO embedding_versions(id, provider, model, dimension, created_at, active, search_text_version) VALUES ('v1', 'https://vectors.example/v1', 'fake', 2, '2026-09-04', 1, 1)")
    assert server._retriever().gateway is gateway
    server._provider_public["chatModel"] = "chat-b"
    assert server._retriever().gateway is gateway  # Chat-only changes preserve the index.
    server._provider_public["embeddingModel"] = "another-model-with-same-dimensions"
    assert server._retriever().gateway is None
    server._provider_public["embeddingModel"] = "fake"
    server._provider_public["embeddingBaseUrl"] = "https://different.example/v1"
    assert server._retriever().gateway is None
    server._provider_public["embeddingBaseUrl"] = "https://vectors.example/v1"
    server._provider_public["embeddingMode"] = "none"
    assert server._retriever().gateway is None
    server._get_importer().close()


def test_local_search_chat_is_persisted_without_provider(tmp_path) -> None:  # type: ignore[no-untyped-def]
    note = tmp_path / "oracle.md"
    note.write_text("# 恢复记录\nPDB 恢复后处于 MOUNTED 状态，需要执行 OPEN。", encoding="utf-8")
    server = WorkerServer(tmp_path / "data")
    imported = json.loads(
        server.process_line(request("import_paths", params={"paths": [str(note)]}))
    )
    assert imported["result"]["summary"]["imported"] == 1

    answer = json.loads(
        server.process_line(request("chat", params={"query": "PDB MOUNTED"}))
    )["result"]
    assert answer["mode"] == "local_search"
    assert answer["citations"][0]["sourceFilename"] == "oracle.md"

    conversations = json.loads(server.process_line(request("list_conversations")))["result"]
    assert conversations["conversations"][0]["id"] == answer["conversationId"]
    detail = json.loads(
        server.process_line(
            request("get_conversation", params={"conversationId": answer["conversationId"]})
        )
    )["result"]["conversation"]
    assert [message["role"] for message in detail["messages"]] == ["user", "assistant"]
    assert detail["messages"][1]["citations"][0]["citationId"] == "S1"


def test_import_emits_persisted_job_progress(tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[dict] = []
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "one.txt").write_text("第一份资料", encoding="utf-8")
    (notes / "two.txt").write_text("第二份资料", encoding="utf-8")
    server = WorkerServer(tmp_path / "data", event_sink=events.append)

    response = json.loads(
        server.process_line(request("import_paths", params={"paths": [str(notes)]}))
    )

    progress = [event for event in events if event["event"] == "job_progress"]
    assert response["result"]["summary"]["imported"] == 2
    assert progress[0]["data"]["progress"] == 0
    assert progress[-1]["data"]["progress"] == 1
    assert progress[-1]["data"]["currentStep"] == "处理完成"
    assert all(event["requestId"] == "test-1" for event in progress)


def test_worker_emits_stream_tokens_before_verified_chat_response(tmp_path) -> None:  # type: ignore[no-untyped-def]
    events: list[dict] = []
    note = tmp_path / "oracle.md"
    note.write_text("PDB 恢复后处于 MOUNTED 状态。", encoding="utf-8")
    server = WorkerServer(tmp_path / "data", event_sink=events.append)
    server.process_line(request("import_paths", params={"paths": [str(note)]}))
    server._gateway = StreamingGateway()

    response = json.loads(
        server.process_line(request("chat", params={"query": "PDB MOUNTED", "stream": True}))
    )

    chat_events = [event for event in events if event["event"] == "chat_token"]
    assert [event["data"]["token"] for event in chat_events] == ["回答", "。[S1]"]
    assert response["result"]["answer"] == "回答。[S1]"
    assert response["result"]["citations"][0]["citationId"] == "S1"
