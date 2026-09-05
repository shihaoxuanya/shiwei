from pathlib import Path

import pytest
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from reportlab.lib.pdfencrypt import StandardEncryption
from PIL import Image, ImageDraw, ImageFont

from shiwei_ai.ingestion import Importer
from shiwei_ai.ingestion.indexer import LexicalSearch
from shiwei_ai.ingestion.parser import DocumentParser, ParseError


def make_pdf(path: Path, *, scanned: bool = False, encrypted: bool = False) -> None:
    encryption = StandardEncryption("locked", ownerPassword="owner") if encrypted else None
    document = canvas.Canvas(str(path), pagesize=(600, 800), encrypt=encryption)
    for text in ("Oracle recovery runbook", "PDB MOUNTED - ALTER PLUGGABLE DATABASE OPEN"):
        if scanned:
            image = Image.new("RGB", (1200, 1600), "white")
            draw = ImageDraw.Draw(image)
            font_path = Path("C:/Windows/Fonts/arial.ttf")
            font = ImageFont.truetype(str(font_path), 38) if font_path.exists() else ImageFont.load_default(size=38)
            draw.text((80, 120), text, fill="black", font=font)
            document.drawImage(ImageReader(image), 0, 0, width=600, height=800)
        else:
            document.setFont("Helvetica", 18)
            document.drawString(40, 740, text)
        document.showPage()
    document.save()


def test_text_pdf_import_is_offline_searchable_and_page_located(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    pdf = tmp_path / "恢复记录.PDF"
    make_pdf(pdf)
    importer = Importer(tmp_path / "data")
    report = importer.import_paths([str(pdf)])
    assert report["summary"]["imported"] == 1, report["failed"]
    hits = LexicalSearch(importer.database).search("MOUNTED")
    assert hits[0]["pageNumber"] == 2
    assert importer.import_paths([str(pdf)])["summary"]["skipped"] == 1
    importer.close()


def test_scanned_pdf_uses_local_ocr(tmp_path):
    pdf = tmp_path / "scan.pdf"
    make_pdf(pdf, scanned=True)
    result = DocumentParser().parse(pdf, pdf.name)
    text = " ".join(block.text for section in result.sections for block in section.blocks)
    assert "MOUNTED" in text
    assert result.sections[-1].page_number == 2


def test_password_protected_pdf_has_actionable_error(tmp_path):
    pdf = tmp_path / "locked.pdf"
    make_pdf(pdf, encrypted=True)
    with pytest.raises(ParseError, match="密码|加密"):
        DocumentParser().parse(pdf, pdf.name)


def test_corrupt_pdf_does_not_break_other_imports(tmp_path):
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"not a PDF")
    note = tmp_path / "note.txt"
    note.write_text("Still searchable", encoding="utf-8")
    importer = Importer(tmp_path / "data")
    report = importer.import_paths([str(pdf), str(note)])
    assert report["summary"] == {"imported": 1, "skipped": 0, "failed": 1}
    importer.close()


def test_failed_pdf_can_be_added_again_after_parser_repair(tmp_path, monkeypatch):
    pdf = tmp_path / "retry.pdf"
    make_pdf(pdf)
    importer = Importer(tmp_path / "data")
    original_parse = importer.indexer.parser.parse
    def unavailable(*args):
        raise ParseError("旧解析模型缺失")
    monkeypatch.setattr(importer.indexer.parser, "parse", unavailable)
    assert importer.import_paths([str(pdf)])["summary"]["failed"] == 1
    source_id = importer.list_sources()[0]["id"]
    monkeypatch.setattr(importer.indexer.parser, "parse", original_parse)
    report = importer.import_paths([str(pdf)])
    assert report["summary"] == {"imported": 1, "skipped": 0, "failed": 0}
    assert importer.list_sources()[0]["id"] == source_id
    assert importer.list_sources()[0]["status"] == "searchable"
    importer.close()


def test_pdf_page_limit_and_chinese_content(tmp_path):
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    pdf = tmp_path / "中文资料.pdf"
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    document = canvas.Canvas(str(pdf))
    document.setFont("STSong-Light", 16)
    document.drawString(40, 700, "数据库恢复案例：需要重新打开数据库。")
    document.save()
    parsed = DocumentParser().parse(pdf, pdf.name)
    assert "数据库恢复案例" in parsed.sections[0].blocks[0].text
    many = tmp_path / "large.pdf"
    document = canvas.Canvas(str(many))
    for _ in range(301):
        document.drawString(40, 700, "Page")
        document.showPage()
    document.save()
    with pytest.raises(ParseError, match="300"):
        DocumentParser().parse(many, many.name)
