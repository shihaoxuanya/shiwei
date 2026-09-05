from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from shiwei_ai import __version__
from shiwei_ai.chat.assistant import Assistant, source_card
from shiwei_ai.ingestion import Importer
from shiwei_ai.ingestion.indexer import LexicalSearch
from shiwei_ai.models import ModelGateway, ModelGatewayError, ProviderConfig, create_gateway
from shiwei_ai.notes import NoteService
from shiwei_ai.retrieval import EmbeddingIndexer, HybridRetriever, LanceVectorStore
from shiwei_ai.search_projection import SEARCH_TEXT_VERSION
from shiwei_ai.schemas import PROTOCOL_VERSION, RpcRequest, RpcResponse
from shiwei_ai.storage.database import utc_now

logger = logging.getLogger("shiwei.worker")
Handler = Callable[[dict[str, Any]], dict[str, Any]]


class WorkerMethodError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class WorkerServer:
    """Single-process JSON Lines RPC dispatcher.

    Stdout belongs exclusively to protocol responses. Logging must use stderr or files.
    """

    def __init__(
        self,
        data_dir: Path | None = None,
        event_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.should_stop = False
        self._data_dir = data_dir
        self._importer: Importer | None = None
        self._gateway: ModelGateway | None = None
        self._provider_public: dict[str, Any] | None = None
        self._event_sink = event_sink
        self._active_request_id: str | None = None
        self._handlers: dict[str, Handler] = {
            "ping": self._ping,
            "import_paths": self._import_paths,
            "list_sources": self._list_sources,
            "delete_source": self._delete_source,
            "reindex_source": self._reindex_source,
            "search_lexical": self._search_lexical,
            "search_hybrid": self._search_hybrid,
            "provider_configure": self._provider_configure,
            "provider_test": self._provider_test,
            "provider_models": self._provider_models,
            "provider_status": self._provider_status,
            "rebuild_embeddings": self._rebuild_embeddings,
            "index_status": self._index_status,
            "chat": self._chat,
            "list_conversations": self._list_conversations,
            "get_conversation": self._get_conversation,
            "delete_conversation": self._delete_conversation,
            "list_notes": self._list_notes,
            "get_note": self._get_note,
            "create_note": self._create_note,
            "update_note": self._update_note,
            "delete_note": self._delete_note,
            "shutdown": self._shutdown,
        }

    def process_line(self, line: str) -> str:
        request_id: str | None = None
        try:
            payload = json.loads(line)
            if isinstance(payload, dict) and isinstance(payload.get("id"), str):
                request_id = payload["id"]
            request = RpcRequest.model_validate(payload)
        except json.JSONDecodeError as error:
            response = RpcResponse.failure(None, "PARSE_ERROR", "请求不是有效的 JSON")
            logger.warning("Malformed JSON received: %s", error.msg)
            return self._encode(response)
        except ValidationError as error:
            response = RpcResponse.failure(
                request_id,
                "INVALID_REQUEST",
                "请求格式不符合 Worker 协议",
                {"issues": error.error_count()},
            )
            return self._encode(response)

        handler = self._handlers.get(request.method)
        if handler is None:
            return self._encode(
                RpcResponse.failure(request.id, "METHOD_NOT_FOUND", f"不支持的方法：{request.method}")
            )

        try:
            self._active_request_id = request.id
            return self._encode(RpcResponse.success(request.id, handler(request.params)))
        except WorkerMethodError as error:
            return self._encode(RpcResponse.failure(request.id, error.code, error.message))
        except ModelGatewayError as error:
            return self._encode(RpcResponse.failure(request.id, "PROVIDER_ERROR", str(error)))
        except Exception:
            logger.exception("Worker method failed: %s", request.method)
            return self._encode(
                RpcResponse.failure(request.id, "INTERNAL_ERROR", "Worker 执行请求失败")
            )
        finally:
            self._active_request_id = None

    @staticmethod
    def _encode(response: RpcResponse) -> str:
        payload = response.model_dump(mode="json", exclude_none=True)
        payload["id"] = response.id
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _ping(_: dict[str, Any]) -> dict[str, Any]:
        return {
            "status": "pong",
            "protocolVersion": PROTOCOL_VERSION,
            "workerVersion": __version__,
        }

    def _shutdown(self, _: dict[str, Any]) -> dict[str, Any]:
        if self._importer is not None:
            self._importer.close()
            self._importer = None
        if self._gateway is not None:
            self._close_gateway(self._gateway)
            self._gateway = None
        self.should_stop = True
        return {"status": "stopping"}

    def _get_importer(self) -> Importer:
        if self._importer is None:
            self._importer = Importer(self._data_dir)
        return self._importer

    def _note_service(self) -> NoteService:
        importer = self._get_importer()
        return NoteService(importer.database, importer.data_dir / "parsed")

    def _import_paths(self, params: dict[str, Any]) -> dict[str, Any]:
        paths = params.get("paths")
        if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
            raise ValueError("paths 必须是字符串数组")
        result = self._get_importer().import_paths(paths, self._emit_job_progress)
        if self._has_embedding() and result["summary"]["imported"] > 0:
            try:
                result["embeddingIndex"] = self._embedding_indexer().rebuild()
            except Exception as error:
                logger.exception("Embedding refresh failed after import")
                result["embeddingIndex"] = {
                    "status": "failed",
                    "message": "资料已保存并可全文搜索，但语义索引更新失败",
                    "reason": str(error),
                }
        return result

    def _list_sources(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"sources": self._get_importer().list_sources()}

    def _delete_source(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self._get_importer().delete_source(self._require_source_id(params))
        if self._has_embedding():
            result["embeddingIndex"] = self._embedding_indexer().rebuild()
        return result

    def _reindex_source(self, params: dict[str, Any]) -> dict[str, Any]:
        result = self._get_importer().reindex_source(self._require_source_id(params))
        if self._has_embedding():
            result["embeddingIndex"] = self._embedding_indexer().rebuild()
        return result

    def _search_lexical(self, params: dict[str, Any]) -> dict[str, Any]:
        query = params.get("query")
        if not isinstance(query, str):
            raise ValueError("query 必须是字符串")
        limit = params.get("limit", 20)
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit 必须是 1 到 100 的整数")
        search = LexicalSearch(self._get_importer().database)
        return {"query": query, "hits": search.search(query, limit)}

    def _search_hybrid(self, params: dict[str, Any]) -> dict[str, Any]:
        query = self._require_query(params)
        result = self._retriever().retrieve(query)
        context = result["context"]
        return {
            "query": result["query"],
            "hits": result["fusionResult"],
            "citations": [self._serialize_citation(item) for item in context.citations],
            "channels": {
                "lexical": len(result["lexicalHits"]),
                "semantic": len(result["semanticHits"]),
            },
        }

    def _provider_configure(self, params: dict[str, Any]) -> dict[str, Any]:
        config = self._provider_config(params)
        if not config.chat_model or (config.embedding_mode != "none" and not config.embedding_model):
            raise WorkerMethodError("INVALID_PROVIDER", "请选择模型后保存")
        gateway = create_gateway(config)
        if self._gateway is not None:
            self._close_gateway(self._gateway)
        self._gateway = gateway
        self._provider_public = {
            "baseUrl": config.base_url,
            "chatModel": config.chat_model,
            "embeddingModel": config.embedding_model,
            "providerId": config.provider_id,
            "protocol": config.protocol,
            "embeddingMode": config.embedding_mode,
            "embeddingBaseUrl": config.embedding_base_url,
            "embeddingProviderId": config.embedding_provider_id,
        }
        return {"configured": True, **self._provider_public}

    def _provider_test(self, params: dict[str, Any]) -> dict[str, Any]:
        config = self._provider_config(params)
        gateway = create_gateway(config)
        try:
            return gateway.test_connection()
        finally:
            gateway.close()

    def _provider_models(self, params: dict[str, Any]) -> dict[str, Any]:
        target = params.get("target", "chat")
        if target not in {"chat", "embedding"}:
            raise WorkerMethodError("INVALID_PROVIDER", "模型列表类型不正确")
        gateway = create_gateway(self._provider_config(params))
        try:
            return gateway.list_models(target)
        finally:
            gateway.close()

    def _provider_status(self, _: dict[str, Any]) -> dict[str, Any]:
        return {
            "configured": self._gateway is not None,
            **(self._provider_public or {}),
        }

    def _rebuild_embeddings(self, _: dict[str, Any]) -> dict[str, Any]:
        if self._gateway is None:
            raise WorkerMethodError("PROVIDER_REQUIRED", "请先在设置中配置模型服务")
        return self._embedding_indexer().rebuild()

    def _index_status(self, _: dict[str, Any]) -> dict[str, Any]:
        database = self._get_importer().database
        chunk_count = int(database.connection.execute("SELECT count(*) FROM chunks").fetchone()[0])
        active = database.connection.execute(
            """
            SELECT id, provider, model, dimension, created_at, search_text_version
            FROM embedding_versions WHERE active = 1
            ORDER BY created_at DESC LIMIT 1
            """
        ).fetchone()
        return {
            "chunkCount": chunk_count,
            "embeddingVersionId": active["id"] if active else None,
            "provider": active["provider"] if active else None,
            "model": active["model"] if active else None,
            "dimension": active["dimension"] if active else None,
            "lastIndexedAt": active["created_at"] if active else None,
            "searchTextVersion": active["search_text_version"] if active else None,
            "needsRebuild": bool(active and active["search_text_version"] != SEARCH_TEXT_VERSION),
        }

    def _chat(self, params: dict[str, Any]) -> dict[str, Any]:
        query = self._require_query(params)
        conversation_id = params.get("conversationId")
        if conversation_id is not None and not isinstance(conversation_id, str):
            raise WorkerMethodError("INVALID_CONVERSATION", "对话 ID 不正确")
        history = self._conversation_history(conversation_id) if conversation_id else []
        answer = Assistant(self._retriever(), self._gateway).ask(query, history, self._emit_chat_token if params.get("stream") is True else None)
        answer["citations"] = [self._camelize_citation(item) for item in answer["citations"]]
        answer["conversationId"] = self._save_exchange(conversation_id, query, answer["answer"], answer["citations"], answer)
        return answer

    def _list_conversations(self, params: dict[str, Any]) -> dict[str, Any]:
        query = params.get("query", "")
        offset = params.get("offset", 0)
        if not isinstance(query, str) or len(query) > 200:
            raise WorkerMethodError("INVALID_QUERY", "历史搜索不能超过 200 字")
        if type(offset) is not int or not 0 <= offset <= 100000:
            raise WorkerMethodError("INVALID_OFFSET", "历史对话页码不正确")
        query = query.strip()
        pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = self._get_importer().database.connection.execute(
            """
            SELECT c.id, c.title, c.created_at, c.updated_at,
                   (SELECT substr(m.content, 1, 90) FROM messages m
                    WHERE m.conversation_id = c.id
                    ORDER BY m.created_at DESC, m.rowid DESC LIMIT 1) AS preview
            FROM conversations c
            WHERE ? = '' OR c.title LIKE ? ESCAPE '\\'
               OR EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id
                          AND m.content LIKE ? ESCAPE '\\')
            ORDER BY c.updated_at DESC, c.id DESC LIMIT 50 OFFSET ?
            """,
            (query, pattern, pattern, offset),
        ).fetchall()
        return {
            "conversations": [
                {
                    "id": row["id"],
                    "title": row["title"] or "未命名对话",
                    "createdAt": row["created_at"],
                      "updatedAt": row["updated_at"],
                      "preview": row["preview"] or "",
                }
                for row in rows
            ]
        }

    def _get_conversation(self, params: dict[str, Any]) -> dict[str, Any]:
        conversation_id = params.get("conversationId")
        if not isinstance(conversation_id, str) or not conversation_id:
            raise WorkerMethodError("INVALID_CONVERSATION", "对话 ID 不正确")
        database = self._get_importer().database
        conversation = database.connection.execute(
            "SELECT id, title, created_at, updated_at FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if conversation is None:
            raise WorkerMethodError("CONVERSATION_NOT_FOUND", "没有找到这段对话")
        rows = database.connection.execute(
            "SELECT id, role, content, created_at, error, answer_kind, mode, notice FROM messages WHERE conversation_id = ? ORDER BY created_at, rowid",
            (conversation_id,),
        ).fetchall()
        messages = []
        for row in rows:
            citations = database.connection.execute(
                """
                SELECT c.citation_id, c.document_id, c.chunk_id, c.source_filename,
                       c.page_number, c.sheet_name, c.slide_number, c.heading_path, c.snippet,
                       s.original_path, s.stored_path, s.imported_at, s.source_type,
                       n.id AS note_id, n.created_at AS note_created_at,
                       n.updated_at AS note_updated_at, k.mentioned_dates
                FROM citations c
                JOIN documents d ON d.id = c.document_id
                JOIN sources s ON s.id = d.source_id
                LEFT JOIN notes n ON n.source_id = s.id
                LEFT JOIN chunks k ON k.id = c.chunk_id
                WHERE c.message_id = ? ORDER BY c.citation_id
                """,
                (row["id"],),
            ).fetchall()
            messages.append(
                {
                    "id": row["id"],
                    "role": row["role"],
                    "content": row["content"],
                    "createdAt": row["created_at"],
                    "error": row["error"],
                    "answerKind": row["answer_kind"] or ("knowledge" if citations else "general"),
                    "mode": row["mode"],
                    "notice": row["notice"],
                    "sourceMatches": [source_card(item) for item in database.connection.execute("SELECT s.*, n.id AS note_id, n.created_at AS note_created_at, n.updated_at AS note_updated_at FROM message_sources ms JOIN sources s ON s.id=ms.source_id LEFT JOIN notes n ON n.source_id=s.id WHERE ms.message_id=? ORDER BY ms.ordinal", (row["id"],)).fetchall()],
                    "citations": [
                        {
                            "citationId": item["citation_id"],
                            "documentId": item["document_id"],
                            "chunkId": item["chunk_id"],
                            "sourceFilename": item["source_filename"],
                            "sourcePath": item["original_path"],
                            "storedPath": item["stored_path"],
                            "importedAt": item["imported_at"],
                            "pageNumber": item["page_number"],
                            "sheetName": item["sheet_name"],
                            "slideNumber": item["slide_number"],
                            "headingPath": item["heading_path"],
                            "snippet": item["snippet"],
                            "sourceType": item["source_type"],
                            "noteId": item["note_id"],
                            "noteCreatedAt": item["note_created_at"],
                            "noteUpdatedAt": item["note_updated_at"],
                            "mentionedDates": json.loads(item["mentioned_dates"] or "[]"),
                        }
                        for item in citations
                    ],
                }
            )
        return {
            "conversation": {
                "id": conversation["id"],
                "title": conversation["title"],
                "createdAt": conversation["created_at"],
                "updatedAt": conversation["updated_at"],
                "messages": messages,
            }
        }

    def _delete_conversation(self, params: dict[str, Any]) -> dict[str, Any]:
        conversation_id = params.get("conversationId")
        if not isinstance(conversation_id, str) or not conversation_id:
            raise WorkerMethodError("INVALID_CONVERSATION", "对话 ID 不正确")
        database = self._get_importer().database
        conversation = database.connection.execute(
            "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
        ).fetchone()
        if conversation is None:
            raise WorkerMethodError("CONVERSATION_NOT_FOUND", "没有找到这段对话")
        message_count = int(
            database.connection.execute(
                "SELECT count(*) FROM messages WHERE conversation_id = ?", (conversation_id,)
            ).fetchone()[0]
        )
        citation_count = int(
            database.connection.execute(
                """
                SELECT count(*) FROM citations c
                JOIN messages m ON m.id = c.message_id
                WHERE m.conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()[0]
        )
        with database.transaction() as connection:
            connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return {
            "conversationId": conversation_id,
            "deleted": True,
            "messagesDeleted": message_count,
            "citationsDeleted": citation_count,
        }

    def _list_notes(self, params: dict[str, Any]) -> dict[str, Any]:
        query = params.get("query", "")
        if not isinstance(query, str):
            raise WorkerMethodError("INVALID_QUERY", "笔记搜索内容不正确")
        try:
            return {"notes": self._note_service().list(query)}
        except ValueError as error:
            raise WorkerMethodError("INVALID_NOTE", str(error)) from error

    def _get_note(self, params: dict[str, Any]) -> dict[str, Any]:
        note_id = self._require_note_id(params)
        try:
            return {"note": self._note_service().get(note_id)}
        except ValueError as error:
            raise WorkerMethodError("NOTE_NOT_FOUND", str(error)) from error

    def _create_note(self, _: dict[str, Any]) -> dict[str, Any]:
        return {"note": self._note_service().create()}

    def _update_note(self, params: dict[str, Any]) -> dict[str, Any]:
        note_id = self._require_note_id(params)
        title = params.get("title", "")
        content = params.get("content", "")
        if not isinstance(title, str) or not isinstance(content, str):
            raise WorkerMethodError("INVALID_NOTE", "笔记标题和正文必须是文字")
        try:
            note = self._note_service().update(note_id, title, content)
        except ValueError as error:
            raise WorkerMethodError("INVALID_NOTE", str(error)) from error
        document_id = note.get("documentId")
        previous_document_id = note.pop("previousDocumentId", None)
        if self._has_embedding():
            try:
                if document_id:
                    note["embeddingIndex"] = self._embedding_indexer().replace_document(document_id)
                elif previous_document_id:
                    vector_store = LanceVectorStore(self._get_importer().data_dir / "index" / "lancedb")
                    vector_store.delete_document(previous_document_id)
                    note["embeddingIndex"] = {"indexed": 0}
            except Exception as error:
                logger.exception("Embedding refresh failed after note update")
                note["embeddingIndex"] = {
                    "status": "failed",
                    "message": "笔记已保存并可全文搜索，但语义索引更新失败",
                    "reason": str(error),
                }
        return {"note": note}

    def _delete_note(self, params: dict[str, Any]) -> dict[str, Any]:
        note_id = self._require_note_id(params)
        try:
            result = self._note_service().delete(note_id)
        except ValueError as error:
            raise WorkerMethodError("NOTE_NOT_FOUND", str(error)) from error
        if self._has_embedding() and result.get("documentId"):
            try:
                vector_store = LanceVectorStore(self._get_importer().data_dir / "index" / "lancedb")
                vector_store.delete_document(result["documentId"])
            except Exception as error:
                logger.exception("Embedding cleanup failed after note deletion")
                result["embeddingIndex"] = {
                    "status": "failed",
                    "message": "笔记已删除；失效向量不会进入回答，并将在下次重建时清理",
                    "reason": str(error),
                }
        return result

    def _conversation_history(self, conversation_id: str) -> list[dict[str, str]]:
        if self._get_importer().database.connection.execute("SELECT 1 FROM conversations WHERE id=?", (conversation_id,)).fetchone() is None:
            raise WorkerMethodError("CONVERSATION_NOT_FOUND", "没有找到这段对话，请新建对话后重试")
        rows = self._get_importer().database.connection.execute(
            """
            SELECT id, role, content FROM messages
            WHERE conversation_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 6
            """,
            (conversation_id,),
        ).fetchall()
        connection = self._get_importer().database.connection
        return [{"role": row["role"], "content": row["content"], "sourceIds": [item[0] for item in connection.execute("SELECT source_id FROM message_sources WHERE message_id=? UNION SELECT d.source_id FROM citations c JOIN documents d ON d.id=c.document_id WHERE c.message_id=?", (row["id"], row["id"]))]} for row in reversed(rows)]

    def _save_exchange(
        self,
        conversation_id: str | None,
        query: str,
        answer: str,
        citations: list[dict[str, Any]],
        metadata: dict[str, Any] | None = None,
    ) -> str:
        database = self._get_importer().database
        conversation_id = conversation_id or str(uuid4())
        now = utc_now()
        user_message_id = str(uuid4())
        assistant_message_id = str(uuid4())
        with database.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    (conversation_id, query[:36], now, now),
                )
            else:
                connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conversation_id)
                )
            connection.execute(
                "INSERT INTO messages(id, conversation_id, role, content, created_at) VALUES (?, ?, 'user', ?, ?)",
                (user_message_id, conversation_id, query, now),
            )
            connection.execute(
                "INSERT INTO messages(id, conversation_id, role, content, created_at, answer_kind, mode, notice) VALUES (?, ?, 'assistant', ?, ?, ?, ?, ?)",
                (assistant_message_id, conversation_id, answer, now, (metadata or {}).get("answerKind"), (metadata or {}).get("mode"), (metadata or {}).get("notice")),
            )
            for ordinal, source in enumerate((metadata or {}).get("sourceMatches", [])):
                connection.execute("INSERT INTO message_sources(message_id, source_id, ordinal) VALUES (?, ?, ?)", (assistant_message_id, source["sourceId"], ordinal))
            for citation in citations:
                connection.execute(
                    """
                    INSERT INTO citations(
                      id, message_id, citation_id, document_id, chunk_id,
                      source_filename, page_number, sheet_name, slide_number,
                      heading_path, snippet
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid4()),
                        assistant_message_id,
                        citation["citationId"],
                        citation["documentId"],
                        citation["chunkId"],
                        citation["sourceFilename"],
                        citation.get("pageNumber"),
                        citation.get("sheetName"),
                        citation.get("slideNumber"),
                        citation.get("headingPath"),
                        citation["snippet"],
                    ),
                )
        return conversation_id

    def _emit_chat_token(self, token: str) -> None:
        if self._event_sink is None or not token:
            return
        self._event_sink(
            {
                "jsonrpc": "2.0",
                "protocol_version": PROTOCOL_VERSION,
                "event": "chat_token",
                "requestId": self._active_request_id,
                "data": {"token": token},
            }
        )

    def _emit_job_progress(self, data: dict[str, Any]) -> None:
        if self._event_sink is None:
            return
        self._event_sink(
            {
                "jsonrpc": "2.0",
                "protocol_version": PROTOCOL_VERSION,
                "event": "job_progress",
                "requestId": self._active_request_id,
                "data": data,
            }
        )

    @staticmethod
    def _close_gateway(gateway: ModelGateway) -> None:
        close = getattr(gateway, "close", None)
        if callable(close):
            close()

    def _retriever(self) -> HybridRetriever:
        importer = self._get_importer()
        active = importer.database.connection.execute("SELECT provider, model, search_text_version FROM embedding_versions WHERE active = 1 LIMIT 1").fetchone()
        config = self._provider_public or {}
        use_semantic = self._has_embedding() and active is not None and active["provider"] == self._embedding_identity() and active["model"] == config.get("embeddingModel") and active["search_text_version"] == SEARCH_TEXT_VERSION
        store = LanceVectorStore(importer.data_dir / "index" / "lancedb") if use_semantic else None
        return HybridRetriever(
            importer.database,
            gateway=self._gateway if use_semantic else None,
            semantic_index=store,
        )

    def _has_embedding(self) -> bool:
        config = self._provider_public or {}
        return (
            self._gateway is not None
            and bool(config.get("embeddingModel"))
            and config.get("embeddingMode", "same") != "none"
        )

    def _embedding_identity(self) -> str:
        config = self._provider_public or {}
        return str(config.get("embeddingBaseUrl") if config.get("embeddingMode") == "separate" else config.get("baseUrl") or "openai_compatible")

    def _embedding_indexer(self) -> EmbeddingIndexer:
        if not self._has_embedding():
            raise WorkerMethodError("PROVIDER_REQUIRED", "请先在设置中启用语义检索模型")
        importer = self._get_importer()
        return EmbeddingIndexer(
            importer.database,
            LanceVectorStore(importer.data_dir / "index" / "lancedb"),
            self._gateway,
            provider=self._embedding_identity(),
            batch_size=16,
        )

    @staticmethod
    def _provider_config(params: dict[str, Any]) -> ProviderConfig:
        try:
            return ProviderConfig.model_validate(
                {
                    "base_url": params.get("baseUrl"),
                    "api_key": params.get("apiKey"),
                    "chat_model": params.get("chatModel", ""),
                    "embedding_model": params.get("embeddingModel", ""),
                    "protocol": params.get("protocol", "openai_compatible"),
                    "provider_id": params.get("providerId", "custom"),
                    "embedding_mode": params.get("embeddingMode", "same"),
                    "embedding_base_url": params.get("embeddingBaseUrl") or None,
                    "embedding_api_key": params.get("embeddingApiKey") or None,
                    "embedding_provider_id": params.get("embeddingProviderId", "custom"),
                    "timeout_seconds": params.get("timeoutSeconds", 60),
                }
            )
        except ValidationError as error:
            message = error.errors()[0].get("msg", "模型配置不完整")
            raise WorkerMethodError("INVALID_PROVIDER", str(message)) from error

    @staticmethod
    def _require_query(params: dict[str, Any]) -> str:
        query = params.get("query")
        if not isinstance(query, str) or not query.strip():
            raise WorkerMethodError("INVALID_QUERY", "问题不能为空")
        return " ".join(query.split())

    @staticmethod
    def _serialize_citation(item: Any) -> dict[str, Any]:
        return {
            "citationId": item.citation_id,
            "documentId": item.document_id,
            "chunkId": item.chunk_id,
            "sourceFilename": item.source_filename,
            "sourcePath": item.source_path,
            "storedPath": item.stored_path,
            "importedAt": item.imported_at,
            "pageNumber": item.page_number,
            "sheetName": item.sheet_name,
            "slideNumber": item.slide_number,
            "headingPath": item.heading_path,
            "snippet": item.snippet,
            "sourceType": item.source_type,
            "noteId": item.note_id,
            "noteCreatedAt": item.note_created_at,
            "noteUpdatedAt": item.note_updated_at,
            "mentionedDates": list(item.mentioned_dates),
        }

    @staticmethod
    def _camelize_citation(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "citationId": item["citation_id"],
            "documentId": item["document_id"],
            "chunkId": item["chunk_id"],
            "sourceFilename": item["source_filename"],
            "sourcePath": item.get("source_path"),
            "storedPath": item.get("stored_path"),
            "importedAt": item.get("imported_at"),
            "pageNumber": item.get("page_number"),
            "sheetName": item.get("sheet_name"),
            "slideNumber": item.get("slide_number"),
            "headingPath": item.get("heading_path"),
            "snippet": item["snippet"],
            "sourceType": item.get("source_type", "imported_file"),
            "noteId": item.get("note_id"),
            "noteCreatedAt": item.get("note_created_at"),
            "noteUpdatedAt": item.get("note_updated_at"),
            "mentionedDates": list(item.get("mentioned_dates") or []),
        }

    @staticmethod
    def _require_source_id(params: dict[str, Any]) -> str:
        source_id = params.get("sourceId")
        if not isinstance(source_id, str) or not source_id:
            raise WorkerMethodError("INVALID_SOURCE", "资料 ID 不正确")
        return source_id

    @staticmethod
    def _require_note_id(params: dict[str, Any]) -> str:
        note_id = params.get("noteId")
        if not isinstance(note_id, str) or not note_id:
            raise WorkerMethodError("INVALID_NOTE", "笔记 ID 不正确")
        return note_id
