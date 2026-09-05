"""Evidence gate, evaluated against the original query rather than its rewrites."""
from __future__ import annotations

import re

from shiwei_ai.retrieval.query_plan import QueryPlan, normalize_query


def term_matches(term: str, text: str) -> bool:
    text = normalize_query(text).casefold()
    term = term.casefold()
    if re.search(r"[a-z]", term):
        return bool(re.search(r"(?<![a-z0-9_])" + re.escape(term) + r"(?![a-z0-9_])", text))
    if term == "会议":
        # An equipment manual is not evidence that the user attended a meeting.
        return bool(re.search(r"会议(?!室|桌|椅|设备|系统|软件|平台)", text))
    return term in text


def assess_candidate(hit: dict, plan: QueryPlan) -> dict:
    title = str(hit.get("documentTitle") or hit.get("filename") or "")
    text = title + "\n" + str(hit.get("content", ""))
    matched = [term for term in plan.topic_terms if term_matches(term, text)]
    missing = [term for term in plan.topic_terms if term not in matched]
    dates = list(hit.get("mentionedDates") or [])
    coverage = len(matched) / max(len(plan.topic_terms), 1)
    semantic = max(-1.0, min(1.0, float(hit.get("semanticScore", 0))))
    reasons = []
    if plan.invalid_dates:
        reasons.append("invalid_explicit_date")
    if any(not any(d[5:] == month_day for d in dates) for month_day in plan.requested_month_days):
        reasons.append("explicit_month_day_mismatch")
    if plan.requested_dates and not set(plan.requested_dates).issubset(dates):
        reasons.append("explicit_date_mismatch")
    if any(not any(d.startswith(year + "-") for d in dates) for year in plan.requested_years):
        reasons.append("explicit_year_mismatch")
    if any(term_matches(term, text) for term in plan.negated_terms):
        reasons.append("excluded_topic")
    if not matched:
        reasons.append("no_topical_evidence")
    if any(re.search(r"[a-zA-Z]", term) or term == "会议" for term in missing):
        reasons.append("required_topic_mismatch")
    if plan.temporal_intent and missing:
        reasons.append("temporal_topic_mismatch")
    if plan.temporal_intent and not dates:
        reasons.append("no_explicit_date_evidence")
    if not plan.temporal_intent and coverage < 0.5 and not (semantic >= 0.82 and coverage >= 0.25):
        reasons.append("low_topic_coverage")
    eligible = not reasons
    title_coverage = sum(term_matches(t, title) for t in plan.topic_terms) / max(len(plan.topic_terms), 1)
    return {
        "eligible": eligible, "reasons": reasons, "matchedTerms": matched,
        "missingTerms": missing, "topicCoverage": coverage,
        "score": 0.85 * coverage + 0.15 * max(semantic, 0),
        "titleBoost": 0.012 * title_coverage if eligible else 0,
        "temporalBoost": 0.01 * coverage if eligible and plan.temporal_intent and dates else 0,
    }
