"""Question-specific evidence selection after existing recall/relevance ranking."""
import re


def is_event_recall(query: str) -> bool:
    if re.search(r"还有哪些|相关资料|相关文件|相关文档|所有资料", query):
        return False
    return bool(re.search(r"开(?:了|过)?(?:个|次|场)?会|会上|会里|会议|纪要|会谈", query))


def describes_meeting(hit: dict) -> bool:
    title = str(hit.get("documentTitle") or hit.get("filename") or "")
    body = str(hit.get("content") or "")
    if re.search(r"会议(?!室|桌|椅|系统|设备|软件)|纪要|会谈", title):
        return True
    # A related deployment report may itself document meeting decisions; allow
    # explicit event content, not incidental pointers to some other meeting note.
    return bool(re.search(
        r"(?:本次|此次|那次)会议.{0,24}(?:讨论|围绕|决定|明确|确认|要求)|"
        r"会议(?:讨论了|决定了|明确了|确认了|要求|结论|内容如下)|"
        r"会上(?:讨论|提出|决定|确认|明确)|会议纪要", body,
    ))


def evidence_decision(hit: dict, query: str) -> dict:
    if is_event_recall(query) and not describes_meeting(hit):
        return {"selected_as_evidence": False, "evidence_reason": "topic_related_but_not_event_evidence"}
    if not hit["relevance"]["eligible"]:
        return {"selected_as_evidence": False, "evidence_reason": "retrieval_relevance_gate"}
    return {"selected_as_evidence": True, "evidence_reason": "answers_current_question"}
