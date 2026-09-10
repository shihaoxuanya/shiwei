from __future__ import annotations

import errno
from pathlib import Path
from types import SimpleNamespace

import pytest
from reportlab.pdfgen import canvas

from shiwei_ai.ingestion import Importer
from shiwei_ai.ingestion.chunker import StructureAwareChunker
from shiwei_ai.ingestion.indexer import LexicalSearch
from shiwei_ai.ingestion.limits import MAX_FILE_BYTES, MAX_PDF_PAGES, validate_file_size
from shiwei_ai.ingestion.parser import DocumentParser, ParseError
from shiwei_ai.storage.raw_store import COPY_DISK_RESERVE_BYTES, RawStore


@pytest.mark.parametrize("size", [0, 100 * 1024 ** 2 + 1, MAX_FILE_BYTES])
def test_file_size_boundary_is_inclusive_without_allocating_gigabytes(size):
    validate_file_size(size)


@pytest.mark.parametrize("size", [MAX_FILE_BYTES + 1, 2 * MAX_FILE_BYTES])
def test_larger_than_one_gigabyte_is_rejected(size):
    with pytest.raises(ValueError, match="1GB"):
        validate_file_size(size)


@pytest.mark.parametrize("size", [-1, True, None, 1.5])
def test_invalid_file_size_is_rejected(size):
    with pytest.raises(ValueError, match="文件大小"):
        validate_file_size(size)


@pytest.mark.parametrize("size, reaches_hash", [(MAX_FILE_BYTES, True), (MAX_FILE_BYTES + 1, False)])
def test_importer_checks_one_gigabyte_boundary_before_reading_bytes(tmp_path, monkeypatch, size, reaches_hash):
    path = tmp_path / "boundary.txt"
    path.write_text("small fixture with synthetic metadata", encoding="utf-8")
    importer = Importer(tmp_path / "library")
    original_stat = Path.stat

    def simulated_stat(item, *args, **kwargs):
        if item == path:
            return SimpleNamespace(st_size=size)
        return original_stat(item, *args, **kwargs)

    class HashReached(RuntimeError):
        pass

    def hashing(_):
        raise HashReached("size accepted before hashing")

    monkeypatch.setattr(Path, "stat", simulated_stat)
    monkeypatch.setattr(importer.raw_store, "sha256", hashing)
    try:
        with pytest.raises(HashReached if reaches_hash else ValueError):
            importer._import_file(path)
    finally:
        importer.close()


def _large_pdf(path: Path) -> None:
    """A real 101 MiB PDF with a streamed unused object, not a giant RAM blob."""
    with path.open("wb") as handle:
        handle.write(b"%PDF-1.4\n")
        offsets = [0]

        def obj(number: int, body: bytes) -> None:
            offsets.append(handle.tell())
            handle.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")

        obj(1, b"<< /Type /Catalog /Pages 2 0 R >>")
        obj(2, b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>")
        obj(3, b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 600 800] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>")
        obj(4, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
        content = b"BT /F1 16 Tf 40 700 Td (Largefileproof Oracle recovery 38~50 days) Tj ET"
        obj(5, f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream")
        offsets.append(handle.tell())
        handle.write(f"6 0 obj\n<< /Length {101 * 1024 ** 2} >>\nstream\n".encode())
        block = b"0" * (1024 ** 2)
        for _ in range(101):
            handle.write(block)
        handle.write(b"\nendstream\nendobj\n")
        xref = handle.tell()
        handle.write(b"xref\n0 7\n0000000000 65535 f \n")
        for offset in offsets[1:]:
            handle.write(f"{offset:010d} 00000 n \n".encode())
        handle.write(f"trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())


def _pages_pdf(path: Path, pages: int) -> None:
    document = canvas.Canvas(str(path), pageCompression=1)
    for page in range(1, pages + 1):
        document.drawString(40, 700, f"Pageproof{page:04d} migration 38~50 days")
        document.showPage()
    document.save()


def test_actual_over_100mb_pdf_is_copied_parsed_indexed_and_deduplicated(tmp_path):
    path = tmp_path / "超一百兆资料.pdf"
    _large_pdf(path)
    assert path.stat().st_size > 100 * 1024 ** 2
    original_hash = RawStore.sha256(path)
    importer = Importer(tmp_path / "library")
    try:
        report = importer.import_paths([str(path)])
        assert report["summary"]["imported"] == 1, report
        hit = LexicalSearch(importer.database).search("Largefileproof")[0]
        assert hit["pageNumber"] == 1
        source = importer.list_sources()[0]
        assert RawStore.sha256(Path(source["storedPath"])) == original_hash
        assert source["size"] == path.stat().st_size
        assert importer.import_paths([str(path)])["summary"]["skipped"] == 1
        assert RawStore.sha256(path) == original_hash
    finally:
        importer.close()


def test_actual_301_page_pdf_import_preserves_final_page_citation(tmp_path):
    path = tmp_path / "三百零一页.pdf"
    _pages_pdf(path, 301)
    importer = Importer(tmp_path / "library")
    try:
        assert importer.import_paths([str(path)])["summary"]["imported"] == 1
        hit = LexicalSearch(importer.database).search("Pageproof0301")[0]
        assert hit["pageNumber"] == 301
    finally:
        importer.close()


def test_actual_3000_page_pdf_parses_all_pages_and_preserves_chunk_locations(tmp_path):
    path = tmp_path / "三千页边界.pdf"
    _pages_pdf(path, MAX_PDF_PAGES)
    original_hash = RawStore.sha256(path)
    progress = []
    document = DocumentParser().parse(path, path.name, progress_sink=progress.append)
    assert len(document.sections) == MAX_PDF_PAGES
    assert len(progress) == MAX_PDF_PAGES
    assert progress[0] == {"currentPage": 1, "totalPages": 3000, "stage": "reading"}
    assert progress[-1] == {"currentPage": 3000, "totalPages": 3000, "stage": "reading"}
    assert [event["currentPage"] for event in progress] == list(range(1, MAX_PDF_PAGES + 1))
    last = StructureAwareChunker().chunk(document)[-1]
    assert last.page_number == 3000
    assert "38~50" in last.content
    assert RawStore.sha256(path) == original_hash


def test_actual_3001_page_pdf_rejects_before_parsing_any_page(tmp_path):
    path = tmp_path / "超三千页.pdf"
    _pages_pdf(path, MAX_PDF_PAGES + 1)
    progress = []
    with pytest.raises(ParseError, match="超过 3000 页"):
        DocumentParser().parse(path, path.name, progress_sink=progress.append)
    assert progress == []


def test_parse_without_progress_preserves_two_argument_pdf_wrappers(tmp_path, monkeypatch):
    parser = DocumentParser()
    sentinel = object()
    monkeypatch.setattr(parser, "_parse_pdf", lambda path, filename: sentinel)
    assert parser.parse(tmp_path / "file.pdf", "file.pdf") is sentinel


def test_pdf_ocr_resources_close_even_if_ocr_fails(tmp_path, monkeypatch):
    import pypdfium2

    closed = []
    text = SimpleNamespace(get_text_range=lambda: "", close=lambda: closed.append("text"))
    image = SimpleNamespace(close=lambda: closed.append("image"))
    bitmap = SimpleNamespace(to_pil=lambda: image, close=lambda: closed.append("bitmap"))
    page = SimpleNamespace(get_textpage=lambda: text, get_size=lambda: (600, 800), render=lambda **_: bitmap, close=lambda: closed.append("page"))

    class Pdf:
        def __len__(self):
            return 1

        def __getitem__(self, _):
            return page

        def close(self):
            closed.append("pdf")

    monkeypatch.setattr(pypdfium2, "PdfDocument", lambda _: Pdf())
    parser = DocumentParser()

    def broken_ocr(_):
        raise ParseError("本地 OCR 识别失败")

    monkeypatch.setattr(parser, "_ocr_text", broken_ocr)
    with pytest.raises(ParseError, match="OCR"):
        parser.parse(tmp_path / "scan.pdf", "scan.pdf")
    assert closed == ["text", "image", "bitmap", "page", "pdf"]


@pytest.mark.parametrize("spare, succeeds", [(-1, False), (0, True), (1, True)])
def test_copy_checks_destination_space_with_reserve(tmp_path, monkeypatch, spare, succeeds):
    from shiwei_ai.storage import raw_store

    path = tmp_path / "original.txt"
    path.write_text("immutable source", encoding="utf-8")
    store = RawStore(tmp_path / "raw")
    content_hash = store.sha256(path)
    monkeypatch.setattr(raw_store.shutil, "disk_usage", lambda _: SimpleNamespace(free=path.stat().st_size + COPY_DISK_RESERVE_BYTES + spare))
    if succeeds:
        assert store.copy(path, content_hash).read_bytes() == path.read_bytes()
    else:
        with pytest.raises(OSError, match="空间不足") as error:
            store.copy(path, content_hash)
        assert error.value.errno == errno.ENOSPC
        assert list(store.root.rglob("*")) == []
    assert path.read_text(encoding="utf-8") == "immutable source"


def test_existing_copy_does_not_require_additional_free_space(tmp_path, monkeypatch):
    from shiwei_ai.storage import raw_store

    path = tmp_path / "original.txt"
    path.write_text("source", encoding="utf-8")
    store = RawStore(tmp_path / "raw")
    content_hash = store.sha256(path)
    copied = store.copy(path, content_hash)
    monkeypatch.setattr(raw_store.shutil, "disk_usage", lambda _: pytest.fail("Must not recheck space for duplicate content"))
    assert store.copy(path, content_hash) == copied


def test_copy_running_out_of_space_removes_only_own_temporary_file(tmp_path, monkeypatch):
    from shiwei_ai.storage import raw_store

    path = tmp_path / "original.txt"
    path.write_text("immutable source", encoding="utf-8")
    store = RawStore(tmp_path / "raw")
    retained = store.root / "previous.txt"
    retained.write_text("existing library", encoding="utf-8")

    def interrupted_copy(_, destination):
        Path(destination).write_bytes(b"partial")
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(raw_store.shutil, "copy2", interrupted_copy)
    content_hash = store.sha256(path)
    with pytest.raises(OSError, match="空间不足"):
        store.copy(path, content_hash)
    assert not store.destination(content_hash, path.suffix).exists()
    assert list(store.root.rglob("*.tmp")) == []
    assert retained.read_text(encoding="utf-8") == "existing library"
    assert path.read_text(encoding="utf-8") == "immutable source"
