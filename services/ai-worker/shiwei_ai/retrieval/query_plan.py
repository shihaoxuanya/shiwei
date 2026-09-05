"""Small, deterministic query planning for personal recall.

This module only produces retrieval inputs. It does not call a model, answer a
question, add event dates, or alter stored source text. The original query stays
available to the relevance gate so a broader rewrite cannot relax its constraints.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import date

from shiwei_ai.search_terms import STOP_WORDS, search_terms


_EXPLICIT_DATE = re.compile(
    r"(?<!\d)(?P<year>\d{4})(?:年\s*(?P<cm>\d{1,2})月\s*(?P<cd>\d{1,2})[日号]?"
    r"|-(?P<im>\d{1,2})-(?P<id>\d{1,2}))(?!\d)"
)
_PARTIAL_DATE = re.compile(r"(?<!\d)(?P<month>\d{1,2})月\s*(?P<day>\d{1,2})[日号](?!\d)")
_YEAR = re.compile(r"(?<!\d)(\d{4})年")
_TEMPORAL_QUESTION = re.compile(r"几号|哪[一]?天|什么时候|何时|哪[一]?日|日期|时间|哪[一]?年|哪[一]?月|几月")
_NEGATION = re.compile(r"不是|不包括|不含|排除|不要|并非|没有|没[有]?|未|不曾|不再|不存在")
_NEGATIVE_CLAUSE = re.compile(r"(?:不是|不包括|不含|排除|不要|并非)(.+?)(?=而是|[，,。；;！？!?]|$)")
_SCAFFOLD = re.compile(
    r"还有哪些|还有什么|相关资料|相关文件|相关文档|"
    r"还记不记得|记不记得|记不清|还记得|记得|大概|大约|大致|好像|似乎|可能|"
    r"之前那个|之前那次|之前|以前|此前|上次|当时|曾经|过去|"
    r"什么时候|日期时间|几号|哪一天|哪天|哪一日|何时|日期|时间|时候|"
    r"讨论过|讨论了|讨论|聊过|聊了|聊|提到过|提到|谈过|谈到|谈了|"
    r"参加过|参加|参与过|参与|开过|是否|有没有|是不是|了吗|了么|"
    r"不存在|存在|帮我|帮忙|请问|请|告诉我|告诉|想知道|知道|总结|概括|解释|介绍|说明|"
    r"我[们]?|咱们|自己|本人|这个|那个|这次|那次|这场|那场|一下"
)
_NON_TOPIC = {
    "日期", "时间", "日期时间", "时候", "几号", "哪天", "记录", "笔记", "资料",
    "文件", "文档", "内容", "开了个", "是在", "什么", "一下", "相关", "查询",
    "回忆", "检索", "日子", "发生", "具体", "准确", "明确", "进行", "多少",
    "不是", "不要", "排除", "不含", "不包括", "没有", "并非", "不曾", "不再", "多久", "多长", "说了",
    "你好", "您好", "谢谢", "hello", "hi",
}
_GENERIC_NEGATION = {"会议", "记录", "笔记", "文件", "文档", "资料", "项目"}


@dataclass(frozen=True)
class QueryPlan:
    original_query: str
    rewritten_queries: tuple[str, ...]
    topic_terms: tuple[str, ...]
    requested_dates: tuple[str, ...]
    temporal_intent: bool
    personal_recall: bool
    requested_years: tuple[str, ...] = ()
    negated_terms: tuple[str, ...] = ()
    requested_month_days: tuple[str, ...] = ()
    invalid_dates: tuple[str, ...] = ()

    @property
    def all_queries(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.original_query, *self.rewritten_queries)))


def normalize_query(query: str) -> str:
    """Normalize conversational verbs without changing entities or polarity."""
    text = unicodedata.normalize("NFKC", query).strip()
    text = re.sub(r"会上|会里", "会议", text)
    text = re.sub(
        r"开(?:了|过)?(?:一?个|一?次|一?场)(?:很)?(?:重要|临时|紧急|大型|小型|简短)的?会",
        "会议", text,
    )
    text = re.sub(r"开(?:了|过)?(?:一?个|一?次|一?场)?会", "会议", text)
    text = re.sub(r"召开(?:了|过)?(?:一?次|一?场)?会议", "会议", text)
    text = re.sub(r"什么时候|哪一天|哪天|哪一日|何时|几号", "日期时间", text)
    text = re.sub(r"讨论[过了]|聊[过了]|谈[过了]", "讨论", text)
    text = re.sub(r"提到过", "提到", text)
    return re.sub(r"\s+", " ", text).strip()


def _dates(text: str) -> tuple[str, ...]:
    found = []
    for match in _EXPLICIT_DATE.finditer(text):
        try:
            value = date(
                int(match["year"]), int(match["cm"] or match["im"]),
                int(match["cd"] or match["id"]),
            ).isoformat()
        except ValueError:
            # An invalid date is not evidence for an event date.
            continue
        found.append(value)
    return tuple(dict.fromkeys(found))


def _other_date_constraints(text: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    full_dates = list(_EXPLICIT_DATE.finditer(text))
    invalid = [match.group(0) for match in full_dates if not _dates(match.group(0))]
    partial = []
    for match in _PARTIAL_DATE.finditer(text):
        if any(full.start() <= match.start() < full.end() for full in full_dates):
            continue
        try:
            # A leap year validates possible month/day values; it is never
            # returned as an event year or included in any retrieval query.
            value = date(2000, int(match["month"]), int(match["day"])).strftime("%m-%d")
        except ValueError:
            invalid.append(match.group(0))
            continue
        partial.append(value)
    return tuple(dict.fromkeys(partial)), tuple(dict.fromkeys(invalid))


def _topics(text: str) -> tuple[str, ...]:
    text = _EXPLICIT_DATE.sub(" ", text)
    text = _PARTIAL_DATE.sub(" ", text)
    text = _YEAR.sub(" ", text)
    text = _SCAFFOLD.sub(" ", text)
    terms = tuple(dict.fromkeys(
        term for term in search_terms(text)
        if term.casefold() not in _NON_TOPIC and not term.isdigit()
    ))
    # Unknown short Chinese words may be split into single characters by the
    # bundled dictionary (e.g. 容灾, 张总). Preserve a short lexical clause only
    # when no extracted term covers it; do not concatenate long sentences into
    # brittle exact-match constraints.
    short_clauses = re.split(r"[^\u4e00-\u9fff]+|[的是在与和及并吗呢么了过啊]", text)
    supplements = tuple(
        clause for clause in short_clauses
        if 2 <= len(clause) <= 4 and clause not in _NON_TOPIC and clause not in STOP_WORDS
        and not any(term in clause for term in terms)
    )
    terms = tuple(dict.fromkeys((*terms, *supplements)))
    # Jieba's search mode emits both 数据 and 数据库. Count the complete concept
    # once, rather than turning subwords into additional relevance requirements.
    return tuple(term for term in terms if not any(
        term != other and term in other and re.fullmatch(r"[\u4e00-\u9fff]+", other)
        for other in terms
    ))


def plan_query(query: str) -> QueryPlan:
    original = query.strip()
    normalized = normalize_query(original)
    requested_dates = _dates(normalized)
    requested_month_days, invalid_dates = _other_date_constraints(normalized)
    years = tuple(dict.fromkeys((*_YEAR.findall(normalized), *(d[:4] for d in requested_dates))))
    temporal_intent = bool(_TEMPORAL_QUESTION.search(normalized))

    # Explicit exclusion clauses are not positive relevance anchors. Their
    # distinctive terms are retained for the gate, and every rewritten query
    # retains the full sentence whenever any negation is present.
    excluded = [match.group(1).strip() for match in _NEGATIVE_CLAUSE.finditer(normalized)]
    positive = _NEGATIVE_CLAUSE.sub(" ", normalized)
    topics = _topics(positive)
    negated_terms = tuple(dict.fromkeys(
        term for clause in excluded for term in _topics(clause)
        if term not in _GENERIC_NEGATION and term.casefold() not in {t.casefold() for t in topics}
    ))
    personal_recall = bool(re.search(
        r"我的|我(?:们)?(?:曾|过去|以前|之前|去年|今年|在|做过|参与|上次|开会|开了个会|讨论过)|"
        r"咱们|本人|之前|以前|此前|上次|当时|曾经|过去|讨论过|开过会|开了个会|"
        r"记录|笔记|资料|文件|简历", original
    )) or bool((requested_dates or requested_month_days or invalid_dates) and topics) or bool(temporal_intent and topics)

    rewritten: list[str] = []
    if topics or requested_dates or requested_month_days or invalid_dates or years:
        # Keep year-only constraints explicit; never guess the missing month/day.
        # Preserve literal date expressions too: a missing year or an invalid
        # calendar date must not silently become a broader year-only query.
        date_literals = tuple(match.group(0) for match in _EXPLICIT_DATE.finditer(normalized))
        partial_dates = tuple(re.findall(r"(?<!\d)\d{1,2}月\s*\d{1,2}[日号]", normalized))
        constraints = (
            *topics, *requested_dates,
            *(literal for literal in date_literals if not _dates(literal)),
            *(literal for literal in partial_dates if not any(literal in full for full in date_literals)),
            *(f"{y}年" for y in years if not any(d.startswith(y) for d in requested_dates)),
        )
        focus = " ".join(dict.fromkeys(constraints))
        temporal_suffix = " 日期 时间" if temporal_intent else ""
        if _NEGATION.search(normalized):
            variants = (
                normalized,
                f"检索条件：{normalized}；关键词：{focus}{temporal_suffix}",
                f"查找符合以下条件的原始记录：{normalized}",
            )
        else:
            variants = (
                normalized,
                f"{focus}{temporal_suffix}".strip(),
                f"查找 {focus} 的相关记录" + ("和明确日期" if temporal_intent else ""),
            )
        for variant in variants:
            if variant and variant != original and variant not in rewritten:
                rewritten.append(variant)
        # A one-word topic can equal both normalized and keyword variants.
        # Keep two distinct retrieval formulations without inventing a new topic.
        if len(rewritten) < 2:
            rewritten.append(f"原文中关于 {normalized} 的记录")

    return QueryPlan(
        original_query=original,
        rewritten_queries=tuple(rewritten),
        topic_terms=topics,
        requested_dates=requested_dates,
        temporal_intent=temporal_intent,
        personal_recall=personal_recall,
        requested_years=years,
        negated_terms=negated_terms,
        requested_month_days=requested_month_days,
        invalid_dates=invalid_dates,
    )
