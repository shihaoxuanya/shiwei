from __future__ import annotations

import json
from typing import Any, Protocol

from shiwei_ai.ingestion.indexer import LexicalSearch
from shiwei_ai.models import ModelGateway, ModelGatewayError
from shiwei_ai.retrieval.context import BuiltContext, ContextBuilder
from shiwei_ai.retrieval.query_plan import plan_query
from shiwei_ai.retrieval.relevance import assess_candidate
from shiwei_ai.retrieval.evidence import evidence_decision
from shiwei_ai.storage import Database


class SemanticIndex(Protocol):
    def search(self, vector: list[float], limit: int = 20) -> list[dict[str, Any]]: ...


class RetrievalResult(dict[str, Any]):
    @property
    def context(self) -> BuiltContext:
        return self["context"]


class HybridRetriever:
    def __init__(
        self, database: Database, *, gateway: ModelGateway | None = None,
        semantic_index: SemanticIndex | None = None, fts_limit: int = 20,
        vector_limit: int = 20, final_limit: int = 12,
        debug: bool = False, debug_include_content: bool = False,
    ) -> None:
        self.database = database
        self.lexical = LexicalSearch(database)
        self.gateway = gateway
        self.semantic_index = semantic_index
        self.fts_limit = fts_limit
        self.vector_limit = vector_limit
        self.final_limit = final_limit
        self.debug = debug
        self.debug_include_content = debug_include_content

    def retrieve(self, query: str) -> RetrievalResult:
        plan = plan_query(query)
        queries = plan.all_queries
        embeddings = None
        semantic_error = None
        if self.gateway is not None and self.semantic_index is not None:
            try:
                embeddings = self.gateway.embed(list(queries)).vectors
                if len(embeddings) != len(queries):
                    embeddings = None
                    semantic_error = "invalid_embedding_count"
            except ModelGatewayError:
                # A failed optional semantic provider must not disable local recall.
                semantic_error = "embedding_unavailable"

        fused: dict[str, dict] = {}
        lexical_all: dict[str, dict] = {}
        semantic_all: dict[str, dict] = {}
        runs = []
        for query_index, variant in enumerate(queries):
            lexical_hits = self.lexical.search(variant, self.fts_limit)
            semantic_hits = []
            if embeddings is not None and self.semantic_index is not None:
                try:
                    semantic_hits = self._hydrate_semantic_hits(
                        self.semantic_index.search(embeddings[query_index], self.vector_limit)
                    )
                except (OSError, ValueError, RuntimeError):
                    # Derived vector indexes can be rebuilt; keep local recall usable.
                    semantic_error = "vector_index_unavailable"
                    embeddings = None
            if self.debug:
                runs.append({"query": variant, "ftsHits": [self._trace_hit(h) for h in lexical_hits],
                             "vectorHits": [self._trace_hit(h) for h in semantic_hits]})
            for channel, hits, aggregate in (("fts", lexical_hits, lexical_all), ("vector", semantic_hits, semantic_all)):
                seen = set()
                for rank, hit in enumerate(hits, 1):
                    key = str(hit["chunkId"])
                    if key in seen:
                        continue
                    seen.add(key)
                    aggregate.setdefault(key, hit)
                    if key not in fused:
                        fused[key] = {**hit, "rrfScore": 0.0, "provenance": []}
                    candidate = fused[key]
                    if "semanticScore" in hit:
                        candidate["semanticScore"] = max(candidate.get("semanticScore", -1), hit["semanticScore"])
                    candidate["rrfScore"] += 1.0 / (60 + rank) / len(queries)
                    candidate["provenance"].append({"queryIndex": query_index, "channel": channel, "rank": rank})

        ranked = list(fused.values())
        for hit in ranked:
            hit["relevance"] = assess_candidate(hit, plan)
            hit.update(evidence_decision(hit, query))
            hit["score"] = hit["rrfScore"] + hit["relevance"]["titleBoost"] + hit["relevance"]["temporalBoost"]
        ranked.sort(key=lambda h: (h["score"], h["relevance"]["score"], h["chunkId"]), reverse=True)
        eligible = [h for h in ranked if h["selected_as_evidence"]][:self.final_limit]
        context = ContextBuilder(max_chunks=self.final_limit).build(eligible)
        selected_ids = {c.chunk_id for c in context.citations}
        for hit in ranked:
            if hit["selected_as_evidence"] and hit["chunkId"] not in selected_ids:
                hit.update(selected_as_evidence=False, evidence_reason="context_budget_or_duplicate")
        selected = [h for h in eligible if h["chunkId"] in selected_ids]
        result = RetrievalResult(query=plan.original_query, lexicalHits=list(lexical_all.values()),
                                 semanticHits=list(semantic_all.values()), fusionResult=selected,
                                 context=context, abstained=not bool(selected))
        if self.debug:
            result["debugTrace"] = {
                "version": "retrieval-0.2.1", "originalQuery": plan.original_query,
                "rewrittenQueries": list(plan.rewritten_queries), "topicTerms": list(plan.topic_terms),
                "requestedDates": list(plan.requested_dates), "temporalIntent": plan.temporal_intent,
                "requestedMonthDays": list(plan.requested_month_days), "invalidDates": list(plan.invalid_dates),
                "queryRuns": runs, "fusionResults": [self._trace_hit(h) for h in ranked],
                "selectedContext": [self._trace_hit(h) for h in selected],
                "abstained": not bool(selected), "semanticError": semantic_error,
            }
        return result

    def _trace_hit(self, hit: dict) -> dict:
        keys = ("chunkId", "documentId", "noteId", "sourceType", "documentTitle", "filename",
                "mentionedDates", "lexicalScore", "semanticScore", "semanticDistance", "similarityMetric",
                "matchedBy", "rrfScore", "provenance", "score", "relevance", "selected_as_evidence", "evidence_reason")
        result = {key: hit[key] for key in keys if key in hit}
        if self.debug_include_content:
            result["content"] = hit.get("content", "")
        return result

    def _hydrate_semantic_hits(self, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        hits = []
        for candidate in candidates:
            row = self.database.connection.execute(
                """SELECT c.id AS chunk_id, c.document_id, c.content, c.search_text, c.mentioned_dates,
                          c.page_number, c.sheet_name, c.slide_number, c.heading_path,
                          d.title, s.original_filename, s.original_path, s.stored_path, s.imported_at,
                          s.id AS source_id, s.source_type, n.id AS note_id,
                          n.created_at AS note_created_at, n.updated_at AS note_updated_at
                   FROM chunks c JOIN documents d ON d.id=c.document_id
                   JOIN sources s ON s.id=d.source_id LEFT JOIN notes n ON n.source_id=s.id
                   WHERE c.id=?""", (candidate["chunkId"],),
            ).fetchone()
            if row is None:
                continue
            hits.append({
                "chunkId": row["chunk_id"], "documentId": row["document_id"], "content": row["content"],
                "searchText": row["search_text"], "mentionedDates": json.loads(row["mentioned_dates"] or "[]"),
                "documentTitle": row["title"], "filename": row["original_filename"],
                "sourcePath": row["original_path"], "storedPath": row["stored_path"],
                "importedAt": row["imported_at"], "sourceId": row["source_id"], "sourceType": row["source_type"],
                "noteId": row["note_id"], "noteCreatedAt": row["note_created_at"], "noteUpdatedAt": row["note_updated_at"],
                "pageNumber": row["page_number"], "sheetName": row["sheet_name"],
                "slideNumber": row["slide_number"], "headingPath": row["heading_path"],
                **{key: candidate[key] for key in ("semanticScore", "semanticDistance", "similarityMetric") if key in candidate},
            })
        return hits
