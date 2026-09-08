from __future__ import annotations

import logging
import mimetypes
import os
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
                result = self._import_file(path)
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

    def reindex_source(self, source_id: str) -> dict[str, Any]:
        source = self.database.connection.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if source is None:
            raise ValueError("没有找到这份资料")
        if source["source_type"] != "imported_file":
            raise ValueError("笔记会在编辑时自动更新索引")
        result = self.indexer.index_source(
            source_id,
            Path(source["stored_path"]),
            source["original_filename"],
        )
        return {"sourceId": source_id, **result}

    def delete_source(self, source_id: str) -> dict[str, Any]:
        source = self.database.connection.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if source is None:
            raise ValueError("没有找到这份资料")
        if source["source_type"] != "imported_file":
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
                logger.warning("Unable to remove stored file: %s", candidate)
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

    def _import_file(self, path: Path) -> dict[str, Any]:
        stat = path.stat()
        if stat.st_size > 100 * 1024 * 1024:
            raise ValueError("文件超过 100MB，请拆分或压缩后添加")
        content_hash = self.raw_store.sha256(path)
        existing = self.database.source_by_hash(content_hash)
        if existing is not None:
            if existing["status"] == "failed":
                result = self.reindex_source(existing["id"])
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
        index_result = self.indexer.index_source(source_id, stored_path, path.name)
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
        }
