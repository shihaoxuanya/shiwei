import pytest

from shiwei_ai.text_fidelity import normalize_numeric_ranges
from shiwei_ai.chat.answer_policy import group_citations, fidelity_issues, prepare_answer


@pytest.mark.parametrize('raw,expected', [
    ('38~50天，7~14天，100~200GB', '38～50 天，7～14 天，100～200GB'),
    (r'38\~50天', '38～50 天'),
    ('38～50天', '38～50 天'),
    ('-1.5~2.5小时', '-1.5～2.5 小时'),
    ('你好~ ~/data ~变量~ ~~删除~~', '你好~ ~/data ~变量~ ~~删除~~'),
    ('`38~50`\n```sh\nx=7~14\n```\n    100~200GB', '`38~50`\n```sh\nx=7~14\n```\n    100~200GB'),
    ('~~~txt\n38~50\n~~~', '~~~txt\n38~50\n~~~'),
    ('https://example.test/38~50 [原样](./7~14) <span data-x="7~14">', 'https://example.test/38~50 [原样](./7~14) <span data-x="7~14">'),
])
def test_narrow_range_normalization(raw, expected):
    assert normalize_numeric_ranges(raw) == expected
    assert normalize_numeric_ranges(expected) == expected


@pytest.mark.parametrize('text', [
    '单次38～50天。[N1] 至少两轮。[N1]',
    '- 单次38～50天。[N1]\n- 至少两轮。[N1]\n- 确认环境。[N1]',
    '1. 单次38～50天。[N1]\n\n2. 至少两轮。[N1]\n\n3. 确认环境。[N1]',
])
def test_same_source_paragraph_or_contiguous_list_is_cited_once(text):
    assert group_citations(text).count('[N1]') == 1
    assert group_citations(group_citations(text)) == group_citations(text)


def test_grouping_preserves_distinct_claim_sources_and_code():
    text = '- 会议38～50天。[N1]\n- 部署参数8GB。[S1]\n- 另一页7～14天。[N2]\n- 另一事实。[N1]'
    assert group_citations(text) == text
    literal = '`[N1] [N1]`\n\n```txt\n[N1] [N1]\n```'
    assert group_citations(literal) == literal
    assert group_citations('事实。[N1]\n\n另一个段落。[N1]') == '事实。[N1]\n\n另一个段落。[N1]'


def test_source_fact_and_explicit_calculation_have_different_policy():
    source = '单次38~50天，至少两轮。增量7~14天。'
    assert fidelity_issues(prepare_answer('单次38~50天。[N1]'), source) == []
    correct = '单次38~50天。[N1]\n\n如果两轮串行且每轮相同，简单计算76~100天，这不是原文直接给出的总工期。'
    assert fidelity_issues(prepare_answer(correct), source) == []
    assert 'unlabelled_derived_figure' in fidelity_issues('会议说项目总工期76～100 天。[N1]', source)
    assert 'collapsed_numeric_range' in fidelity_issues('3850天和714天。[N1]', source)


def test_guard_does_not_find_new_scalar_inside_another_number_or_code():
    assert 'unlabelled_derived_figure' in fidelity_issues('76天。[N1]', '工期176天。')
    assert fidelity_issues('示例代码 `duration=76天`。[N1]', '示例代码') == []


def test_same_source_counts_arabic_and_chinese_equally():
    assert fidelity_issues('至少2轮。[N1]', '至少两轮。') == []


def test_cited_introduction_groups_only_same_source_factual_list():
    text = '会议讨论以下内容：[N1]：\n\n- 单次38～50天。[N1]\n- 至少两轮。[N1]'
    assert group_citations(text).count('[N1]') == 1
    multi = text.replace('至少两轮。[N1]', '部署参数。[S1]')
    assert group_citations(multi) == multi
    inference = text.replace('至少两轮。', '如果串行，我的推断是两轮。')
    assert group_citations(inference).count('[N1]') == 2


def test_unsourced_hedged_explanation_is_still_an_inference():
    source = '增量追平需7～14天，工具TMS、OGG待选。'
    unsafe = '增量7～14天。[N1]\n\n具体耗时可能会受数据变更量影响。[N1]'
    assert 'unlabelled_speculation' in fidelity_issues(unsafe, source)
    safe = '增量7～14天。[N1]\n\n我的推断是：耗时可能会受数据变更量影响；这不是原文直接结论。'
    assert fidelity_issues(safe, source) == []
