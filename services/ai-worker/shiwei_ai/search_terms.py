"""Offline search terms; jieba's dictionary ships in the worker, never downloaded."""
import logging
import re
from functools import lru_cache

import jieba

jieba.setLogLevel(logging.ERROR)
STOP_WORDS = set("我的 我 你 你的 帮我 帮 请 请问 一下 什么 怎么 怎样 如何 哪 哪个 哪里 在哪 在哪里 有没有 是否 多少 为什么 以前 之前 曾经 过去 内容 里面 相关 这个 那个 这些 那些 一个 一份 找到 查找 搜索 找 帮忙 总结 概括 介绍 告诉 解释 说明 可以 需要 使用 有什么 有哪些 有 我们 已经 进行 pdf doc docx txt md ppt pptx xls xlsx".split())


@lru_cache(maxsize=256)
def search_terms(query: str) -> tuple[str, ...]:
    # Keep error codes and complete filenames intact, including mixed scripts.
    literals = re.findall(r"[\w\-]+\.(?:pdf|docx?|pptx?|xlsx?|txt|md)\b|[A-Za-z][A-Za-z0-9_+\-]*", query, re.I)
    words = list(jieba.cut_for_search(query[:2000], HMM=False))
    if re.fullmatch(r"[\u4e00-\u9fff]{2,4}", query.strip()):
        literals.append(query.strip())
    return tuple(dict.fromkeys(w.strip() for w in [*literals, *words] if len(w.strip()) >= 2 and w.strip().lower() not in STOP_WORDS and re.search(r"[\w]", w)))[:16]


def like_pattern(term: str) -> str:
    return "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
