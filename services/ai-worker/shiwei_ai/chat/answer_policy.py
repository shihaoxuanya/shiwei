"""Small post-generation guard: numeric fidelity and conservative citation grouping."""
import re
from decimal import Decimal

from shiwei_ai.text_fidelity import PROTECTED, RANGE, normalize_numeric_ranges

CITE = re.compile(r"\[([SN]\d+)\]")
INFERENCE = re.compile(r"推算|估算|推断|假设|简单计算|按.{0,30}计算|如果|若.{0,30}串行")
DISCLAIMER = re.compile(r"(?:不是|并非|非|不等于|不能.{0,12}当作|不代表).{0,25}(?:原文|来源|会议|项目总工期|总工期)|原文.{0,12}(?:未|没有).{0,12}(?:给出|明确|说明)")
ATTRIBUTION = re.compile(r"(?:会议|原文|记录|来源).{0,8}(?:明确|说|表明|给出|记载|确定|指出|要求)")
MEASURED = re.compile(r"(?<![A-Za-z0-9_.])([+-]?\d+(?:\.\d+)?)[ \t]*(天|小时|分钟|秒|周|个月|年|轮|次|GB|TB|MB|KB|%)", re.I)
SPECULATION = re.compile(r"可能(?:会)?受|(?:因此|由此)(?:可见|推知|判断)|取决于|意味着|我的推断|据此推测")


def group_citations(text: str) -> str:
    """Group only a same-ID paragraph/list. Never merge distinct chunk citations."""
    protected = []
    def hide(match):
        protected.append(match[0])
        return f"\x00PROTECTED{len(protected)-1}\x00"
    masked = PROTECTED.sub(hide, text)
    blocks = re.split(r"(\n[ \t]*\n)", masked)
    # Blank lines between bullets still belong to the same contiguous list.
    index = 0
    while index + 2 < len(blocks):
        if re.match(r"^\s*(?:[-*+] |\d+[.)、] )", blocks[index]) and re.match(r"^\s*(?:[-*+] |\d+[.)、] )", blocks[index + 2]):
            blocks[index:index + 3] = ["".join(blocks[index:index + 3])]
        else:
            index += 2
    # A cited introduction ending in a colon can prove its immediately following
    # same-source list. Do not cross headings, mixed sources, or inference blocks.
    for index in range(0, len(blocks) - 2, 2):
        intro, listing = blocks[index], blocks[index + 2]
        intro_ids = CITE.findall(intro)
        if (intro.rstrip().endswith((':', '：')) and intro_ids
                and re.match(r"^\s*(?:[-*+] |\d+[.)、] )", listing)
                and len(set(intro_ids + CITE.findall(listing))) == 1
                and not INFERENCE.search(intro + listing)):
            blocks[index + 2] = CITE.sub('', listing)
    for index in range(0, len(blocks), 2):
        block = blocks[index]
        matches = list(CITE.finditer(block))
        if len(matches) > 1 and len({m[1] for m in matches}) == 1:
            # Keep the last marker, proving the complete paragraph/list above it.
            last = matches[-1].start()
            blocks[index] = CITE.sub(lambda m: m[0] if m.start() == last else "", block)
    result = "".join(blocks)
    for index, span in enumerate(protected):
        result = result.replace(f"\x00PROTECTED{index}\x00", span)
    return result


def prepare_answer(text: str) -> str:
    return group_citations(normalize_numeric_ranges(text)).strip()


def fidelity_issues(answer: str, context_text: str) -> list[str]:
    """Do not guess lost separators or silently turn derived figures into facts.

    This is a numeric guard, not a universal semantic entailment classifier.
    Qualitative inference is governed by the prompt and tested with live examples.
    """
    source = normalize_numeric_ranges(context_text)
    source_compact = re.sub(r"\s+", "", source)
    def in_source(value):
        return bool(re.search(r"(?<![\d.])" + re.escape(value), source_compact))
    # Code and link syntax are literal data, not generated prose assertions.
    answer = PROTECTED.sub("", answer)
    source_ranges = list(RANGE.finditer(source))
    issues = set()
    # Detect the known range-collapse signature even if the model wrote it, not GFM.
    for item in source_ranges:
        unit = (item["unit"] or "").strip()
        collapsed = item["low"] + item["high"]
        if unit and re.search(r"(?<!\d)" + re.escape(collapsed) + r"\s*" + re.escape(unit), answer) and not in_source(collapsed + unit):
            issues.add("collapsed_numeric_range")

    for block in re.split(r"\n\s*\n", answer):
        plain = re.sub(r"[*_#]", "", CITE.sub("", block))
        for clause in re.split(r'[。；;\n]', plain):
            claim = SPECULATION.search(clause)
            if claim and re.sub(r'\s+', '', clause[claim.start():]).strip() not in source_compact:
                if not (INFERENCE.search(plain) and DISCLAIMER.search(plain)):
                    issues.add('unlabelled_speculation')
        new_figures = []
        for item in RANGE.finditer(plain):
            value = re.sub(r"\s+", "", item[0])
            if not in_source(value):
                new_figures.append(item)
        ranges_removed = RANGE.sub("", plain)
        new_scalar = False
        for item in MEASURED.finditer(ranges_removed):
            value = re.sub(r"\s+", "", item[0])
            # Arabic presentation of explicit small Chinese counts is equivalent.
            chinese = {"1": "一", "2": "两", "3": "三", "4": "四", "5": "五", "6": "六", "7": "七", "8": "八", "9": "九", "10": "十"}.get(item[1])
            if not in_source(value) and not (chinese and chinese + item[2] in source_compact):
                new_scalar = True
        if new_figures or new_scalar:
            assertive = re.sub(r"(?:不是|并非|不代表|不等于|非)[^。；;\n]*", "", plain)
            if not (INFERENCE.search(plain) and DISCLAIMER.search(plain)) or ATTRIBUTION.search(assertive):
                issues.add("unlabelled_derived_figure")
            # Verify the specific two-round multiplication without generalizing
            # it to project duration or assuming that the rounds really are serial.
            if re.search(r"两轮|2\s*轮", plain) and re.search(r"全量|两轮", source):
                for item in new_figures:
                    if (item["unit"] or "").strip() == "天" and not any(
                        (s["unit"] or "").strip() == "天"
                        and Decimal(item["low"]) == Decimal(s["low"]) * 2
                        and Decimal(item["high"]) == Decimal(s["high"]) * 2
                        for s in source_ranges
                    ):
                        issues.add("incorrect_two_round_calculation")
    return sorted(issues)
