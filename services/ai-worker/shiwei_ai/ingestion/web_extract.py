"""Offline HTML-to-canonical extraction. HTML is data, never an executable view."""
from __future__ import annotations

import hashlib
import re

from bs4 import BeautifulSoup, Comment, Doctype, Tag

from shiwei_ai.ingestion.canonical import CanonicalBlock, CanonicalDocument, CanonicalSection


class WebImportError(RuntimeError):
    """A deliberately content/URL-free error safe to show in the import UI."""


PARSER_VERSION = "1.0"
_BLOCK_TAGS = {"p", "li", "pre", "table", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}
_AUTH_TITLE = re.compile(r"^(?:登录|登陆|请登录|用户登录|账号登录|身份验证|安全验证|验证码|访问验证|sign[ -]?in|log[ -]?in|access denied|just a moment|attention required)(?:\b|[\s！!。.:：|\-]|$)", re.I)
_UNAVAILABLE = re.compile(r"(?:请(?:先)?(?:开启|启用)\s*(?:javascript|js)|enable javascript|javascript (?:is required|must be enabled)|请完成(?:安全|人机)验证|verify (?:that )?you are human)", re.I)


def _text(tag: Tag) -> str:
    return re.sub(r"\s+", " ", tag.get_text(" ", strip=True)).strip()


def _score(tag: Tag) -> float:
    size = len(_text(tag))
    linked = sum(len(_text(link)) for link in tag.find_all("a"))
    return size - linked * 0.9


def extract_document(raw_html: bytes, *, content_type: str = "", fallback_title: str = "网页") -> CanonicalDocument:
    """Extract inert blocks with stable section indexes for paragraph citations.

    No URL fetching, linked-image loading, JavaScript, CSS or entity expansion is
    performed. Original bytes stay untouched in the separate snapshot archive.
    """
    charset = re.search(r"charset\s*=\s*[\"']?([\w.\-]+)", content_type, re.I)
    try:
        soup = BeautifulSoup(raw_html, "lxml", from_encoding=charset.group(1) if charset else None)
    except Exception:
        raise WebImportError("无法解析网页正文，请将正文粘贴为笔记，或保存文件后导入") from None

    title = _text(soup.title) if soup.title else ""
    has_password = soup.select_one('input[type="password"]') is not None
    has_challenge = soup.select_one('[id*="captcha"], [class*="captcha"], [id*="challenge"]') is not None
    for tag in list(soup.select("script, style, noscript, template, iframe, object, embed, svg, canvas, nav, footer, aside, form, button, input, select, textarea, dialog")):
        if tag.parent is not None:
            tag.decompose()
    for tag in list(soup.select('[hidden], [aria-hidden="true"]')):
        if tag.parent is not None:
            tag.decompose()
    for tag in list(soup.find_all(style=True)):
        if tag.parent is not None and re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", str(tag.get("style")), re.I):
            tag.decompose()
    # Semantic containers are preferred over broad layout divs. A sizeable
    # article wins; short decorative article teasers do not hide a real main.
    candidates = soup.find_all("article")
    article = max(candidates, key=_score, default=None)
    mains = soup.find_all("main") or soup.select('[role="main"]')
    main = max(mains, key=_score, default=None)
    root = article if article is not None and (_score(article) >= 80 or main is None) else main
    if root is None:
        root = soup.body or soup
        for header in list(root.find_all("header")):
            header.decompose()
    headline = root.find("h1")
    title = _text(headline) if headline and _text(headline) else title
    title = (title or fallback_title).strip()[:500]
    visible = _text(root)
    if ((has_password or _AUTH_TITLE.search(title)) and len(visible) < 1500) or (has_challenge and len(visible) < 500) or (_UNAVAILABLE.search(visible) and len(visible) < 500):
        raise WebImportError("该网页需要登录、安全验证或 JavaScript，暂不支持；请粘贴正文或保存文件后导入")

    blocks: list[CanonicalBlock] = []
    headings: list[str] = []
    sections: list[CanonicalSection] = []
    # Recursion captures free text in div-based articles as well as proper HTML
    # paragraphs, without duplicating their nested text.
    def collect(node: Tag) -> None:
        pending: list[str] = []

        def flush() -> None:
            text = re.sub(r"\s+", " ", " ".join(pending)).strip()
            pending.clear()
            if text:
                blocks.append(CanonicalBlock(kind="paragraph", text=text))

        for child in node.children:
            if isinstance(child, (Comment, Doctype)):
                continue
            if not isinstance(child, Tag):
                text = str(child).strip()
                if text:
                    pending.append(text)
                continue
            if child.name in _BLOCK_TAGS:
                flush()
                if child.name == "table":
                    rows = [" | ".join(_text(cell) for cell in row.find_all(["th", "td"], recursive=False)) for row in child.find_all("tr")]
                    text = "\n".join(row for row in rows if row.strip(" |"))
                    kind = "table"
                elif child.name == "pre":
                    text = child.get_text().strip("\n\r")
                    kind = "code"
                else:
                    text = _text(child)
                    kind = "heading" if child.name.startswith("h") else "list" if child.name == "li" else "paragraph"
                if text:
                    blocks.append(CanonicalBlock(kind=kind, text=text))
            elif child.find(list(_BLOCK_TAGS)) or child.name in {"div", "section", "article", "main", "ul", "ol", "header"}:
                flush()
                collect(child)
            elif child.name == "br":
                flush()
            else:
                text = _text(child)
                if text:
                    pending.append(text)
        flush()

    collect(root)
    body = "\n\n".join(block.text for block in blocks if block.kind != "heading")
    if len(re.sub(r"\s", "", body)) < 12:
        raise WebImportError("未找到可保存的网页正文；登录页、空页面和纯动态网页暂不支持")
    for offset, block in enumerate(blocks):
        if block.kind == "heading":
            headings = [block.text]
        block.source_offset = offset
        sections.append(CanonicalSection(heading=headings[-1] if headings else None, heading_path=list(headings), blocks=[block]))
    return CanonicalDocument(title=title, parser="web-html-offline", parser_version=PARSER_VERSION, sections=sections)


def document_body_hash(document: CanonicalDocument) -> str:
    """Version-independent hash of extracted title/body, not parser metadata."""
    text = document.title + "\n\n" + "\n\n".join(section.plain_text() for section in document.sections)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
