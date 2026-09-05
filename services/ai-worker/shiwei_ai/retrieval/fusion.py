from __future__ import annotations

from typing import Any


def reciprocal_rank_fusion(
    lexical_hits: list[dict[str, Any]],
    semantic_hits: list[dict[str, Any]],
    *,
    query: str,
    top_k: int = 12,
    rank_constant: int = 60,
) -> list[dict[str, Any]]:
    """Fuse independently ranked lexical and semantic results.

    Scores are deliberately simple and inspectable for the MVP.
    """

    fused: dict[str, dict[str, Any]] = {}
    debug_positions: dict[str, dict[str, int]] = {}

    for channel, hits in (("lexical", lexical_hits), ("semantic", semantic_hits)):
        for rank, hit in enumerate(hits, start=1):
            chunk_id = hit["chunkId"]
            if chunk_id not in fused:
                fused[chunk_id] = {**hit, "rrfScore": 0.0, "metadataBoost": 0.0}
                debug_positions[chunk_id] = {}
            fused[chunk_id]["rrfScore"] += 1.0 / (rank_constant + rank)
            debug_positions[chunk_id][channel] = rank

    normalized_query = query.casefold().strip()
    exact_terms = [term for term in normalized_query.split() if len(term) >= 2]
    for chunk_id, hit in fused.items():
        title_text = f"{hit.get('filename', '')} {hit.get('documentTitle', '')}".casefold()
        content_text = str(hit.get("content", "")).casefold()
        title_match = any(term in title_text for term in exact_terms)
        exact_content_match = normalized_query and normalized_query in content_text
        boost = (0.006 if title_match else 0.0) + (0.004 if exact_content_match else 0.0)
        hit["metadataBoost"] = boost
        hit["score"] = hit["rrfScore"] + boost
        hit["debug"] = {
            "lexicalRank": debug_positions[chunk_id].get("lexical"),
            "semanticRank": debug_positions[chunk_id].get("semantic"),
            "rrfScore": hit["rrfScore"],
            "metadataBoost": boost,
        }

    return sorted(fused.values(), key=lambda item: item["score"], reverse=True)[:top_k]
