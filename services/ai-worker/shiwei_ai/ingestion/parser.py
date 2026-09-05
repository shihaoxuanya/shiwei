from __future__ import annotations

import csv
import re
from html.parser import HTMLParser
from pathlib import Path

from shiwei_ai.ingestion.canonical import (
    CanonicalBlock,
    CanonicalDocument,
    CanonicalSection,
)


class ParseError(RuntimeError):
    pass


class _StructuredHtmlParser(HTMLParser):
    capture_tags = {"p", "li", "td", "th", "pre", "h1", "h2", "h3", "h4", "h5", "h6"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.active_tag: str | None = None
        self.buffer: list[str] = []
        self.items: list[tuple[str, str]] = []
        self.title: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag == "title":
            self._in_title = True
            self.buffer = []
        elif tag in self.capture_tags:
            self.active_tag = tag
            self.buffer = []

    def handle_data(self, data: str) -> None:
        if self.active_tag or self._in_title:
            self.buffer.append(data)

    def handle_endtag(self, tag: str) -> None:
        text = re.sub(r"\s+", " ", " ".join(self.buffer)).strip()
        if tag == "title" and self._in_title:
            self.title = text or self.title
            self._in_title = False
            self.buffer = []
        elif self.active_tag == tag:
            if text:
                self.items.append((tag, text))
            self.active_tag = None
            self.buffer = []


class DocumentParser:
    version = "1.1"

    def __init__(self) -> None:
        self._ocr_engine = None

    def parse(self, path: Path, original_filename: str) -> CanonicalDocument:
        suffix = path.suffix.lower()
        if suffix in {".md", ".txt"}:
            return self._parse_text(path, original_filename, markdown=suffix == ".md")
        if suffix in {".html", ".htm"}:
            return self._parse_html(path, original_filename)
        if suffix == ".csv":
            return self._parse_csv(path, original_filename)
        if suffix in {".png", ".jpg", ".jpeg", ".webp"}:
            return self._parse_image(path, original_filename)
        if suffix == ".pdf":
            return self._parse_pdf(path, original_filename)
        return self._parse_with_docling(path, original_filename)

    def _ocr_text(self, image: object) -> str:
        try:
            from rapidocr import RapidOCR
            from importlib.resources import files

            if self._ocr_engine is None:
                models = files("rapidocr") / "models"
                paths = {
                    "Det.model_path": models / "PP-OCRv6_det_small.onnx",
                    "Cls.model_path": models / "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
                    "Rec.model_path": models / "PP-OCRv6_rec_small.onnx",
                }
                if not all(path.is_file() for path in paths.values()):
                    raise ParseError("本地 OCR 模型缺失，请修复或重新安装拾微；不会自动上传图片")
                self._ocr_engine = RapidOCR(params={key: str(path) for key, path in paths.items()})
            result = self._ocr_engine(image)
            return "\n".join(str(value).strip() for value in (result.txts or []) if str(value).strip())
        except ParseError:
            raise
        except Exception as error:
            raise ParseError("本地 OCR 识别失败，请检查文件是否清晰、完整") from error

    def _parse_pdf(self, path: Path, original_filename: str) -> CanonicalDocument:
        import pypdfium2 as pdfium

        try:
            pdf = pdfium.PdfDocument(str(path))
        except Exception as error:
            if "password" in str(error).lower() or "security" in str(error).lower():
                raise ParseError("PDF 已加密或需要密码，请先解锁后重新添加") from error
            raise ParseError("无法打开 PDF，文件可能损坏或并非有效 PDF") from error
        sections: list[CanonicalSection] = []
        try:
            if len(pdf) > 300:
                raise ParseError("PDF 超过 300 页，请拆分后添加")
            for index in range(len(pdf)):
                page = pdf[index]
                try:
                    text_page = page.get_textpage()
                    try:
                        text = text_page.get_text_range().replace("\x00", "").strip()
                    finally:
                        text_page.close()
                    kind = "paragraph"
                    if not text:
                        scale = min(2.0, 2400 / max(page.get_size()))
                        bitmap = page.render(scale=scale)
                        try:
                            text = self._ocr_text(bitmap.to_pil())
                        finally:
                            bitmap.close()
                        kind = "image_text"
                    if text:
                        sections.append(CanonicalSection(
                            heading=f"第 {index + 1} 页",
                            heading_path=[f"第 {index + 1} 页"],
                            page_number=index + 1,
                            blocks=[CanonicalBlock(kind=kind, text=text, page_number=index + 1)],
                        ))
                finally:
                    page.close()
        finally:
            pdf.close()
        if not sections:
            raise ParseError("PDF 中没有可提取的文字，扫描页也未识别到正文")
        return CanonicalDocument(title=Path(original_filename).stem, parser="pdfium-local-ocr", parser_version=self.version, sections=sections)

    def _parse_image(self, path: Path, original_filename: str) -> CanonicalDocument:
        try:
            from rapidocr import RapidOCR
        except ImportError as error:
            raise ParseError("图片 OCR 组件未正确安装") from error
        try:
            texts = self._ocr_text(str(path)).splitlines()
        except Exception as error:
            raise ParseError(f"无法识别图片文字：{error}") from error
        if not texts:
            raise ParseError("图片中没有识别到可搜索文字")
        return CanonicalDocument(
            title=Path(original_filename).stem,
            parser="rapidocr",
            parser_version=self.version,
            sections=[
                CanonicalSection(
                    heading="图片文字",
                    heading_path=["图片文字"],
                    page_number=1,
                    blocks=[CanonicalBlock(kind="image_text", text="\n".join(texts), page_number=1)],
                )
            ],
        )

    def _parse_text(
        self, path: Path, original_filename: str, *, markdown: bool
    ) -> CanonicalDocument:
        text = self._read_text(path)
        title = Path(original_filename).stem
        if not markdown:
            return CanonicalDocument(
                title=title,
                parser="plain-text",
                parser_version=self.version,
                sections=[
                    CanonicalSection(
                        heading=None,
                        heading_path=[],
                        blocks=[CanonicalBlock(kind="paragraph", text=text)],
                    )
                ],
            )

        sections: list[CanonicalSection] = []
        heading_stack: list[str] = []
        current = CanonicalSection()
        paragraph: list[str] = []

        def flush_paragraph() -> None:
            if paragraph:
                content = "\n".join(paragraph).strip()
                if content:
                    kind = "code" if content.startswith("```") else "paragraph"
                    current.blocks.append(CanonicalBlock(kind=kind, text=content))
                paragraph.clear()

        def flush_section() -> None:
            flush_paragraph()
            if current.heading is not None or current.blocks:
                sections.append(current.model_copy(deep=True))

        for line in text.splitlines():
            heading_match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
            if heading_match:
                flush_section()
                level = len(heading_match.group(1))
                heading = heading_match.group(2).strip()
                heading_stack[level - 1 :] = [heading]
                current = CanonicalSection(
                    heading=heading,
                    heading_path=heading_stack.copy(),
                    level=level,
                    blocks=[],
                )
                if level == 1 and title == Path(original_filename).stem:
                    title = heading
            elif not line.strip():
                flush_paragraph()
            else:
                paragraph.append(line)
        flush_section()

        if not sections:
            sections.append(
                CanonicalSection(blocks=[CanonicalBlock(kind="paragraph", text=text)])
            )
        return CanonicalDocument(
            title=title,
            parser="markdown-structure",
            parser_version=self.version,
            sections=sections,
        )

    def _parse_html(self, path: Path, original_filename: str) -> CanonicalDocument:
        parser = _StructuredHtmlParser()
        parser.feed(self._read_text(path))
        title = parser.title or Path(original_filename).stem
        sections: list[CanonicalSection] = []
        current = CanonicalSection()
        heading_stack: list[str] = []
        for tag, text in parser.items:
            if tag.startswith("h"):
                if current.heading is not None or current.blocks:
                    sections.append(current)
                level = int(tag[1])
                heading_stack[level - 1 :] = [text]
                current = CanonicalSection(
                    heading=text,
                    heading_path=heading_stack.copy(),
                    level=level,
                )
            else:
                kind = "list" if tag == "li" else "code" if tag == "pre" else "table" if tag in {"td", "th"} else "paragraph"
                current.blocks.append(CanonicalBlock(kind=kind, text=text))
        if current.heading is not None or current.blocks:
            sections.append(current)
        if not sections:
            raise ParseError("HTML 中没有可提取的正文")
        return CanonicalDocument(
            title=title,
            parser="html-structure",
            parser_version=self.version,
            sections=sections,
        )

    def _parse_csv(self, path: Path, original_filename: str) -> CanonicalDocument:
        rows: list[str] = []
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            for row in csv.reader(handle):
                rows.append(" | ".join(cell.strip() for cell in row))
        if not rows:
            raise ParseError("CSV 文件为空")
        return CanonicalDocument(
            title=Path(original_filename).stem,
            parser="csv-structure",
            parser_version=self.version,
            sections=[
                CanonicalSection(
                    heading="表格数据",
                    heading_path=["表格数据"],
                    blocks=[CanonicalBlock(kind="table", text="\n".join(rows))],
                )
            ],
        )

    def _parse_with_docling(self, path: Path, original_filename: str) -> CanonicalDocument:
        try:
            from docling.document_converter import DocumentConverter
        except ImportError as error:
            raise ParseError(
                f"{path.suffix.lower()} 需要 Docling 解析组件，当前开发环境尚未安装"
            ) from error

        try:
            result = DocumentConverter().convert(path)
            document = self._canonical_from_docling(
                result.document,
                original_filename,
                path.suffix.lower(),
            )
        except Exception as error:
            raise ParseError(f"Docling 无法解析该文件：{error}") from error
        if not document.sections:
            raise ParseError("Docling 没有从该文件提取到正文")
        return document

    def _canonical_from_docling(
        self,
        docling_document: object,
        original_filename: str,
        suffix: str,
    ) -> CanonicalDocument:
        title = Path(original_filename).stem
        sections: list[CanonicalSection] = []
        heading_stack: list[str] = []
        current = CanonicalSection()
        current_page: int | None = None

        def flush() -> None:
            nonlocal current
            if current.heading is not None or current.blocks:
                sections.append(current)
            current = CanonicalSection(
                heading=current.heading,
                heading_path=current.heading_path.copy(),
                level=current.level,
            )

        for item, level in docling_document.iterate_items():  # type: ignore[attr-defined]
            label = str(getattr(item, "label", "")).split(".")[-1].casefold()
            text = str(getattr(item, "text", "") or "").strip()
            if not text and item.__class__.__name__.casefold().startswith("table"):
                try:
                    text = str(item.export_to_markdown(doc=docling_document)).strip()
                except Exception:
                    text = ""
            if not text:
                continue

            provenance = list(getattr(item, "prov", []) or [])
            page_number = getattr(provenance[0], "page_no", None) if provenance else None
            source_offset = None
            if provenance:
                charspan = getattr(provenance[0], "charspan", None)
                if charspan:
                    source_offset = int(charspan[0])

            if label in {"title", "section_header", "chapter_title"}:
                flush()
                heading_level = max(1, min(int(level or 1), 6))
                heading_stack[heading_level - 1 :] = [text]
                current = CanonicalSection(
                    heading=text,
                    heading_path=heading_stack.copy(),
                    level=heading_level,
                    page_number=page_number,
                    slide_number=page_number if suffix == ".pptx" else None,
                )
                current_page = page_number
                if label == "title":
                    title = text
                continue

            if current.blocks and page_number is not None and page_number != current_page:
                flush()
            current_page = page_number or current_page
            current.page_number = current.page_number or page_number
            if suffix == ".pptx":
                current.slide_number = current.slide_number or page_number
            kind = self._docling_block_kind(label, item.__class__.__name__)
            current.blocks.append(
                CanonicalBlock(
                    kind=kind,
                    text=text,
                    page_number=page_number,
                    slide_number=page_number if suffix == ".pptx" else None,
                    source_offset=source_offset,
                )
            )
        flush()
        return CanonicalDocument(
            title=title,
            parser="docling",
            parser_version=self.version,
            sections=sections,
        )

    @staticmethod
    def _docling_block_kind(label: str, class_name: str) -> str:
        normalized_class = class_name.casefold()
        if "table" in label or "table" in normalized_class:
            return "table"
        if "code" in label:
            return "code"
        if label in {"list_item", "list"}:
            return "list"
        if "picture" in label or "image" in label:
            return "image_text"
        return "paragraph"

    @staticmethod
    def _read_text(path: Path) -> str:
        try:
            return path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            return path.read_text(encoding="gb18030", errors="replace")
