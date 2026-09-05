from dataclasses import FrozenInstanceError

import pytest

from shiwei_ai.retrieval.query_plan import normalize_query, plan_query


@pytest.mark.parametrize("query", ["会议", "Oracle", "TiDB"])
def test_short_topics_still_have_two_distinct_rewrites(query):
    plan = plan_query(query)
    assert 2 <= len(plan.rewritten_queries) <= 4
    assert len(plan.all_queries) == len(set(plan.all_queries))


@pytest.mark.parametrize("query,topics,dates,temporal", [
    ("2025年9月3日我开会了吗？", ("会议",), ("2025-09-03",), False),
    ("2025年9月3日会议内容是什么？", ("会议",), ("2025-09-03",), False),
    ("我之前开了个会是在几号？", ("会议",), (), True),
    ("之前那个Oracle迁移的会议是哪天？", ("Oracle", "迁移", "会议"), (), True),
    ("我什么时候讨论过TiDB迁移？", ("TiDB", "迁移"), (), True),
])
def test_regression_recall_queries_have_bounded_deterministic_plans(query, topics, dates, temporal):
    plan = plan_query(query)
    assert set(plan.topic_terms) == set(topics)
    assert plan.requested_dates == dates
    assert plan.temporal_intent is temporal
    assert plan.personal_recall
    assert 2 <= len(plan.rewritten_queries) <= 4
    assert plan.all_queries[0] == query
    assert len(plan.all_queries) == len(set(plan.all_queries))
    assert plan == plan_query(query)
    assert all(all(topic in rewritten for topic in topics) for rewritten in plan.rewritten_queries)


def test_query_normalization_does_not_invent_dates_or_technical_entities():
    plan = plan_query("之前那个Kubernetes扩容会议是哪天？")
    assert set(plan.topic_terms) == {"Kubernetes", "扩容", "会议"}
    assert plan.requested_dates == ()
    assert all("Kubernetes" in query and "扩容" in query for query in plan.rewritten_queries)
    assert not any("Oracle" in query or "2025" in query for query in plan.rewritten_queries)


def test_year_only_query_preserves_year_without_making_an_event_date():
    plan = plan_query("2024年不存在的会议是哪天？")
    assert plan.requested_years == ("2024",)
    assert plan.requested_dates == ()
    assert plan.topic_terms == ("会议",)
    assert all("2024年" in query and "不存在" in query for query in plan.rewritten_queries)


def test_explicit_exclusion_remains_in_every_rewrite_and_is_not_positive_topic():
    plan = plan_query("不是Oracle迁移会议，是Kubernetes扩容会议，哪天？")
    assert set(plan.topic_terms) == {"Kubernetes", "扩容", "会议"}
    assert set(plan.negated_terms) == {"Oracle", "迁移"}
    assert all("不是Oracle迁移会议" in query for query in plan.rewritten_queries)


def test_iso_dates_are_validated_and_not_guessed():
    assert plan_query("2025-9-3的会议内容是什么？").requested_dates == ("2025-09-03",)
    assert plan_query("2025年2月30日我开会了吗？").requested_dates == ()
    assert plan_query("9月3日我开会了吗？").requested_dates == ()


def test_error_codes_and_negation_survive_normalization():
    query = "我没有开会，ORA-01034排障记录是什么时候的？"
    normalized = normalize_query(query)
    assert "没有会议" in normalized
    assert "ORA-01034" in normalized
    assert all("没有会议" in variant for variant in plan_query(query).rewritten_queries)


def test_unknown_short_chinese_topics_and_people_are_not_dropped_by_dictionary():
    plan = plan_query("上次和张总聊Redis容灾是哪天？")
    assert set(plan.topic_terms) == {"Redis", "容灾", "张总"}
    assert all("Redis" in query and "容灾" in query and "张总" in query for query in plan.rewritten_queries)


def test_partial_or_invalid_literal_dates_are_not_relaxed_in_rewrites():
    for literal in ("9月3日", "2025年2月30日"):
        plan = plan_query(f"{literal}我开会了吗？")
        assert plan.requested_dates == ()
        assert all(literal in query for query in plan.rewritten_queries)


def test_greeting_is_not_expanded_and_plan_is_immutable():
    plan = plan_query("你好")
    assert plan.rewritten_queries == ()
    assert not plan.personal_recall
    with pytest.raises(FrozenInstanceError):
        plan.original_query = "changed"


@pytest.mark.parametrize("query", ["帮我写一首诗", "请告诉我什么是数据库", "给我解释一下天空为什么是蓝色的"])
def test_polite_general_requests_do_not_become_personal_recall(query):
    assert not plan_query(query).personal_recall


@pytest.mark.parametrize("literal", ["2025年2月30日", "2025-02-30", "9月31日", "13月1日"])
def test_impossible_date_is_explicit_gate_constraint_not_just_a_rewrite(literal):
    plan = plan_query(f"{literal}Oracle迁移会议讲了什么？")
    assert plan.invalid_dates == (literal,)
    assert plan.requested_dates == ()
    assert plan.requested_month_days == ()
    assert plan.personal_recall


@pytest.mark.parametrize("literal,expected", [("9月4日", "09-04"), ("2月29号", "02-29")])
def test_partial_date_is_month_day_constraint_without_an_inferred_year(literal, expected):
    plan = plan_query(f"{literal}Oracle迁移会议讲了什么？")
    assert plan.requested_month_days == (expected,)
    assert plan.requested_dates == ()
    assert plan.requested_years == ()
    assert plan.invalid_dates == ()
    assert all("2000" not in query for query in plan.all_queries)


def test_full_date_does_not_also_create_a_partial_constraint():
    plan = plan_query("2025年9月3日Oracle迁移会议讲了什么？")
    assert plan.requested_dates == ("2025-09-03",)
    assert plan.requested_month_days == ()
    assert plan.invalid_dates == ()


@pytest.mark.parametrize("filler", ["大概", "大约", "大致", "好像", "记不清"])
def test_uncertainty_language_is_not_a_required_topic(filler):
    plan = plan_query(f"我{filler}之前开了个会是在几号？")
    assert plan.topic_terms == ("会议",)
    assert plan.temporal_intent


def test_modified_conversational_meeting_phrase_keeps_meeting_topic():
    plan = plan_query("我之前开了个重要的会是在几号？")
    assert plan.topic_terms == ("会议",)
    assert plan.temporal_intent
