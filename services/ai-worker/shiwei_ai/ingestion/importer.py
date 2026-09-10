from __future__ import annotations

import logging
import base64
import hashlib
import json
import mimetypes
import os
import tempfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from platformdirs import user_data_path

from shiwei_ai.storage.database import Database, utc_now
from shiwei_ai.storage.raw_store import RawStore
from shiwei_ai.storage.library_location import configured_data_dir
from shiwei_ai.storage.library_lock import LibraryLock
from shiwei_ai.ingestion.indexer import DocumentIndexer
from shiwei_ai.ingestion.canonical import CanonicalDocument
from shiwei_ai.ingestion.limits import validate_file_size
from shiwei_ai.ingestion.web_fetch import fetch_snapshot, WebImportError

logger = logging.getLogger("shiwei.ingestion")

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".pptx",
    ".xlsx",
    ".md",
    ".txt",
    ".html",
    ".htm",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
}


def default_data_dir() -> Path:
    selected = configured_data_dir()
    if selected is not None:
        return selected
    configured = os.environ.get("SHIWEI_DATA_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return user_data_path("Shiwei", appauthor=False) / "data"


class Importer:
    def __init__(self, data_dir: Path | None = None) -> None:
        self.data_dir = (data_dir or default_data_dir()).resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._lease = LibraryLock(self.data_dir)
        self._closed = False
        try:
            self.raw_store = RawStore(self.data_dir / "raw")
            for directory in ["parsed", "index", "cache", "logs"]:
                (self.data_dir / directory).mkdir(parents=True, exist_ok=True)
            self.database = Database(self.data_dir / "shiwei.db")
            self.indexer = DocumentIndexer(self.database, self.data_dir / "parsed")
            self.database.recover_interrupted_jobs()
        except Exception:
            if hasattr(self, "database"):
                self.database.close()
            self._lease.close()
            raise

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.database.close()
        finally:
            self._lease.close()

    def import_paths(
        self,
        paths: list[str],
        progress_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        job_id = str(uuid4())
        files, discovery_failures = self._discover(paths)
        self.database.create_job(job_id, "IMPORT", {"paths": paths})
        self.database.update_job(
            job_id,
            status="running",
            progress=0,
            current_step="正在读取文件",
        )
        self._notify_progress(
            progress_sink,
            job_id=job_id,
            progress=0,
            current_step="正在读取文件",
            processed=0,
            total=len(files),
        )

        imported: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        failed = list(discovery_failures)
        total = max(len(files), 1)

        for index, path in enumerate(files, start=1):
            self._notify_progress(
                progress_sink, job_id=job_id, progress=(index - 1) / total,
                current_step=f"正在理解资料 {index}/{len(files)}" + ("（扫描 PDF 的本地识别可能需要稍等）" if path.suffix.lower() == ".pdf" else ""),
                processed=index - 1, total=len(files), filename=path.name,
            )
            try:
                def page_progress(page: dict[str, Any]) -> None:
                    current, count = page["currentPage"], page["totalPages"]
                    step = f"正在{'识别扫描页' if page['stage'] == 'ocr' else '解析 PDF'} {current}/{count} 页"
                    fraction = (index - 1 + 0.9 * current / max(count, 1)) / total
                    self.database.update_job(job_id, status="running", progress=fraction, current_step=step)
                    self._notify_progress(progress_sink, job_id=job_id, progress=fraction, current_step=step,
                                          processed=index - 1, total=len(files), filename=path.name)
                result = self._import_file(path, page_progress)
                (skipped if result["status"] == "duplicate" else imported).append(result)
            except Exception as error:
                failed.append({"path": str(path), "reason": self._safe_error(error)})
            self.database.update_job(
                job_id,
                status="running",
                progress=index / total,
                current_step=f"正在保存资料 {index}/{len(files)}",
            )
            self._notify_progress(
                progress_sink,
                job_id=job_id,
                progress=index / total,
                current_step=f"正在理解资料 {index}/{len(files)}",
                processed=index,
                total=len(files),
                filename=path.name,
            )

        self.database.update_job(
            job_id,
            status="success",
            progress=1,
            current_step="资料已保存，等待理解",
        )
        self._notify_progress(
            progress_sink,
            job_id=job_id,
            progress=1,
            current_step="处理完成",
            processed=len(files),
            total=len(files),
        )
        return {
            "jobId": job_id,
            "imported": imported,
            "skipped": skipped,
            "failed": failed,
            "summary": {
                "imported": len(imported),
                "skipped": len(skipped),
                "failed": len(failed),
            },
        }

    def list_sources(self) -> list[dict[str, Any]]:
        return [self._serialize_source(source) for source in self.database.list_sources()]

    def import_url(self, url: str, progress_sink=None) -> dict[str, Any]:
        """Capture once. Reprocessing and citation viewing never access the network."""
        job_id = str(uuid4())
        self.database.create_job(job_id, "IMPORT", {"kind": "web_page"})
        report: dict[str, Any] = {"jobId": job_id, "imported": [], "skipped": [], "failed": []}
        def progress(value: float, step: str) -> None:
            self.database.update_job(job_id, status="running", progress=value, current_step=step)
            self._notify_progress(progress_sink, job_id=job_id, progress=value, current_step=step, processed=0, total=1)
        try:
            progress(0, "正在读取公开网页")
            snapshot = fetch_snapshot(url)
            progress(0.5, "正在保存网页正文到本机")
            identity = hashlib.sha256(("web_page\0" + snapshot.original_url + "\0" + snapshot.body_hash).encode()).hexdigest()
            existing = self.database.source_by_hash(identity)
            if existing is not None:
                result = {"sourceId": existing["id"], "filename": existing["original_filename"], "path": url}
                if existing["status"] == "failed":
                    result.update(self.reindex_source(existing["id"]))
                    report["imported"].append({**result, "status": "imported"})
                else:
                    report["skipped"].append({**result, "status": "duplicate"})
            else:
                # Archive has a non-executable extension. Only canonical plain blocks cross IPC.
                archive = {"schemaVersion": 1, "originalUrl": snapshot.original_url, "finalUrl": snapshot.final_url,
                           "capturedAt": snapshot.captured_at, "bodyHash": snapshot.body_hash,
                           "rawHtmlBase64": base64.b64encode(snapshot.raw_html).decode("ascii"),
                           "document": snapshot.document.model_dump(mode="json")}
                with tempfile.TemporaryDirectory(dir=self.data_dir / "cache", prefix="web-") as temp:
                    archive_path = Path(temp) / "snapshot.websnapshot"
                    archive_path.write_text(json.dumps(archive, ensure_ascii=False), encoding="utf-8")
                    archive_size = archive_path.stat().st_size
                    stored = self.raw_store.copy(archive_path, self.raw_store.sha256(archive_path))
                source_id = str(uuid4())
                try:
                    self.database.insert_source({"id": source_id, "original_path": "", "original_filename": snapshot.document.title,
                        "stored_path": str(stored), "content_hash": identity, "size": archive_size, "mime_type": "text/html",
                        "created_at": snapshot.captured_at, "imported_at": utc_now(), "status": "processing", "error": None,
                        "source_type": "web_page", "original_url": snapshot.original_url, "final_url": snapshot.final_url,
                        "captured_at": snapshot.captured_at, "body_hash": snapshot.body_hash})
                except Exception:
                    stored.unlink(missing_ok=True)
                    raise
                progress(0.7, "正在建立网页正文索引")
                indexed = self.indexer.index_source(source_id, stored, snapshot.document.title, document=snapshot.document)
                report["imported"].append({"status": "imported", "sourceId": source_id, "path": url,
                    "filename": snapshot.document.title, "contentHash": identity, "chunkCount": indexed["chunkCount"]})
        except Exception as error:
            # Never log network URLs, response bodies or request credentials (including exception strings).
            reason = str(error) if isinstance(error, WebImportError) else (
                "磁盘空间或权限不足，无法保存网页，请检查资料库位置后重试" if isinstance(error, OSError)
                else "网页保存失败，请稍后重试；已保存的资料不受影响")
            report["failed"].append({"path": url, "inputKind": "url", "reason": reason})
        report["summary"] = {key: len(report[key]) for key in ("imported", "skipped", "failed")}
        self.database.update_job(job_id, status="failed" if report["failed"] else "success", progress=1, current_step="网页保存失败" if report["failed"] else "网页已保存")
        self._notify_progress(progress_sink, job_id=job_id, progress=1, current_step="网页保存失败" if report["failed"] else "网页已保存", processed=1, total=1)
        return report

    def _web_document(self, source) -> CanonicalDocument:
        try:
            archive = json.loads(Path(source["stored_path"]).read_text(encoding="utf-8"))
            if archive.get("schemaVersion") != 1:
                raise ValueError
            return CanonicalDocument.model_validate(archive["document"])
        except (OSError, ValueError, KeyError):
            raise ValueError("本地网页快照缺失或损坏，请重新添加原网页链接") from None

    def get_web_snapshot(self, source_id: str) -> dict[str, Any]:
        source = self.database.connection.execute("SELECT * FROM sources WHERE id=? AND source_type='web_page'", (source_id,)).fetchone()
        if source is None:
            raise ValueError("没有找到已保存的网页")
        document = self._web_document(source)
        chunks = self.database.connection.execute(
            "SELECT c.id, s.ordinal FROM chunks c JOIN sections s ON s.id=c.section_id JOIN documents d ON d.id=c.document_id WHERE d.source_id=? ORDER BY c.chunk_index", (source_id,)).fetchall()
        return {"sourceId": source_id, "title": document.title, "originalUrl": source["original_url"],
                "finalUrl": source["final_url"], "capturedAt": source["captured_at"],
                "sections": [{"heading": section.heading, "headingPath": section.heading_path,
                              "blocks": [{"kind": block.kind, "text": block.text} for block in section.blocks]} for section in document.sections],
                "chunks": [{"chunkId": row["id"], "sectionIndex": row["ordinal"]} for row in chunks]}

    def reindex_source(self, source_id: str, progress_sink=None) -> dict[str, Any]:
        source = self.database.connection.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if source is None:
            raise ValueError("没有找到这份资料")
        if source["source_type"] not in ("imported_file", "web_page"):
            raise ValueError("笔记会在编辑时自动更新索引")
        extra = {"document": self._web_document(source)} if source["source_type"] == "web_page" else {}
        if progress_sink is not None:
            extra["progress_sink"] = progress_sink
        result = self.indexer.index_source(
            source_id,
            Path(source["stored_path"]),
            source["original_filename"],
            **extra,
        )
        return {"sourceId": source_id, **result}

    def delete_source(self, source_id: str) -> dict[str, Any]:
        source = self.database.connection.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if source is None:
            raise ValueError("没有找到这份资料")
        if source["source_type"] not in ("imported_file", "web_page"):
            raise ValueError("请在笔记页面删除这条笔记")
        document = self.database.connection.execute(
            "SELECT id, canonical_path FROM documents WHERE source_id = ?", (source_id,)
        ).fetchone()
        with self.database.transaction() as connection:
            if document is not None:
                connection.execute("DELETE FROM chunks_fts WHERE document_id = ?", (document["id"],))
                connection.execute(
                    "DELETE FROM chunks_fts_trigram WHERE document_id = ?", (document["id"],)
                )
            connection.execute("DELETE FROM sources WHERE id = ?", (source_id,))

        candidates = [Path(source["stored_path"])]
        if document is not None:
            candidates.append(Path(document["canonical_path"]))
        for candidate in candidates:
            try:
                candidate.unlink(missing_ok=True)
            except OSError:
                logger.warning("Unable to remove a managed source artifact")
        return {"sourceId": source_id, "deleted": True}

    def _discover(self, paths: list[str]) -> tuple[list[Path], list[dict[str, str]]]:
        files: list[Path] = []
        failures: list[dict[str, str]] = []
        seen: set[Path] = set()

        for raw_path in paths:
            path = Path(raw_path).expanduser()
            if not path.exists():
                failures.append({"path": str(path), "reason": "文件或文件夹不存在"})
                continue
            candidates = path.rglob("*") if path.is_dir() else [path]
            for candidate in candidates:
                if not candidate.is_file():
                    continue
                if candidate.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    if not path.is_dir():
                        failures.append({"path": str(candidate), "reason": "暂不支持这种文件格式"})
                    continue
                resolved = candidate.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    files.append(resolved)

        files.sort(key=lambda item: str(item).casefold())
        return files, failures

    def _import_file(self, path: Path, progress_sink=None) -> dict[str, Any]:
        stat = path.stat()
        validate_file_size(stat.st_size)
        content_hash = self.raw_store.sha256(path)
        existing = self.database.source_by_hash(content_hash)
        if existing is not None:
            if existing["status"] == "failed":
                result = self.reindex_source(existing["id"], progress_sink)
                return {"status": "imported", "path": str(path), "sourceId": existing["id"], "filename": existing["original_filename"], "chunkCount": result["chunkCount"]}
            return {
                "status": "duplicate",
                "path": str(path),
                "sourceId": existing["id"],
                "filename": existing["original_filename"],
            }

        stored_path = self.raw_store.copy(path, content_hash)
        source_id = str(uuid4())
        created_at = datetime.fromtimestamp(stat.st_ctime, tz=UTC).isoformat()
        values = {
            "id": source_id,
            "original_path": str(path),
            "original_filename": path.name,
            "stored_path": str(stored_path),
            "content_hash": content_hash,
            "size": stat.st_size,
            "mime_type": mimetypes.guess_type(path.name)[0],
            "created_at": created_at,
            "imported_at": utc_now(),
            "status": "processing",
            "error": None,
        }
        self.database.insert_source(values)
        extra = {"progress_sink": progress_sink} if progress_sink is not None else {}
        index_result = self.indexer.index_source(source_id, stored_path, path.name, **extra)
        return {
            "status": "imported",
            "path": str(path),
            "sourceId": source_id,
            "filename": path.name,
            "contentHash": content_hash,
            "chunkCount": index_result["chunkCount"],
        }

    @staticmethod
    def _safe_error(error: Exception) -> str:
        if isinstance(error, PermissionError):
            return "没有读取该文件的权限"
        if isinstance(error, MemoryError):
            return "内存不足，请关闭其他程序或拆分文件后重试"
        return str(error) or error.__class__.__name__

    @staticmethod
    def _notify_progress(
        sink: Callable[[dict[str, Any]], None] | None,
        *,
        job_id: str,
        progress: float,
        current_step: str,
        processed: int,
        total: int,
        filename: str | None = None,
    ) -> None:
        if sink is None:
            return
        try:
            sink(
                {
                    "jobId": job_id,
                    "progress": max(0.0, min(progress, 1.0)),
                    "currentStep": current_step,
                    "processed": processed,
                    "total": total,
                    "filename": filename,
                }
            )
        except Exception:
            logger.exception("Import progress sink failed")

    @staticmethod
    def _serialize_source(source: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": source["id"],
            "originalPath": source["original_path"],
            "filename": source["original_filename"],
            "storedPath": source["stored_path"],
            "contentHash": source["content_hash"],
            "size": source["size"],
            "mimeType": source["mime_type"],
            "importedAt": source["imported_at"],
            "status": source["status"],
            "error": source["error"],
            "sourceType": source["source_type"],
            "originalUrl": source.get("original_url"),
            "finalUrl": source.get("final_url"),
            "capturedAt": source.get("captured_at"),
            "bodyHash": source.get("body_hash"),
        }
