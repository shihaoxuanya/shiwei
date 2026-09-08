"""Verified local library relocation; originals are never moved or deleted.

Only sources.stored_path and documents.canonical_path are storage pointers.
Notes, messages, original_path, job payloads, canonical JSON and vectors remain
byte/content-identical. The external location file is the final commit point.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
import errno
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from shiwei_ai.storage.library_lock import LOCK_FILENAME, LibraryLock
from shiwei_ai.storage.raw_store import RawStore

if TYPE_CHECKING:
    from shiwei_ai.ingestion import Importer


class LibraryLocationError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(message: str, code: str = "LIBRARY_DESTINATION_INVALID") -> None:
    raise LibraryLocationError(code, message)


def _is_link(path: Path) -> bool:
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0)
                                           & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _check_ancestors(path: Path) -> None:
    for part in (path, *path.parents):
        if part.exists() or part.is_symlink():
            if _is_link(part):
                _fail("资料库位置不能包含符号链接、目录联接或其他重解析点。")


def _absolute_path(value: object) -> Path:
    if not isinstance(value, str) or not value.strip() or "\x00" in value:
        _fail("请选择有效的本地文件夹。")
    path = Path(value)
    if not path.is_absolute() or value.startswith(("\\\\", "//")) or ".." in path.parts:
        _fail("请选择本机的绝对文件夹路径，不支持网络路径或父目录跳转。")
    _check_ancestors(path)
    return path.resolve()


def location_config_path() -> Path | None:
    value = os.environ.get("SHIWEI_LOCATION_CONFIG")
    return _absolute_path(value) if value else None


def configured_data_dir() -> Path | None:
    config = location_config_path()
    return _read_location(config)


def _read_location(config: Path | None) -> Path | None:
    if config is None or not config.exists():
        return None
    try:
        if not config.is_file() or config.stat().st_size > 16_384:
            raise ValueError()
        payload = json.loads(config.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("version") != 1:
            raise ValueError()
        selected = _absolute_path(payload.get("dataDir"))
        if not selected.is_dir() or not (selected / "shiwei.db").is_file():
            _fail("已选择的资料库不存在或不可访问。请连接原磁盘并重试；不会创建空资料库。", "LIBRARY_LOCATION_MISSING")
        _check_ancestors(selected / "shiwei.db")
        return selected
    except LibraryLocationError:
        raise
    except (OSError, ValueError, TypeError) as error:
        raise LibraryLocationError("LIBRARY_LOCATION_INVALID", "资料库位置配置损坏或无法读取。请恢复位置配置后重试；不会改用空资料库。") from error


def _destination_parent(value: object, source: Path) -> Path:
    parent = _absolute_path(value)
    if not parent.is_dir():
        _fail("目标文件夹不存在或不是文件夹，请重新选择。")
    if parent == Path(parent.anchor) or parent.is_relative_to(source):
        _fail("不能选择磁盘根目录、当前资料库或其内部文件夹。")
    if os.name == "nt":
        protected = [Path(value).resolve() for key in ("SystemRoot", "WINDIR", "ProgramFiles", "ProgramFiles(x86)")
                     if (value := os.environ.get(key))]
    else:
        protected = [Path(value) for value in ("/System", "/Library", "/usr", "/bin", "/sbin", "/etc", "/dev", "/proc", "/sys", "/private/etc")]
    if any(parent.is_relative_to(path) for path in protected):
        _fail("不能把资料库放入系统目录，请选择个人文件夹或其他数据磁盘上的文件夹。")
    return parent


def _inventory(root: Path) -> tuple[list[Path], list[Path]]:
    files, directories = [], []
    pending = [root]
    while pending:
        directory = pending.pop()
        _check_ancestors(directory)
        for path in directory.iterdir():
            if _is_link(path):
                _fail("资料库中存在符号链接或目录联接，迁移已停止；原资料库保持不变。", "LIBRARY_UNSAFE_ENTRY")
            if path.is_dir():
                directories.append(path.relative_to(root))
                pending.append(path)
            elif path.is_file():
                if path.parent == root and path.name in {LOCK_FILENAME, "shiwei.db", "shiwei.db-wal", "shiwei.db-shm", "shiwei.db-journal"}:
                    continue
                files.append(path.relative_to(root))
            else:
                _fail("资料库包含不支持的特殊文件，迁移已停止。", "LIBRARY_UNSAFE_ENTRY")
    return sorted(files), sorted(directories)


def _managed_path(value: str, root: Path, folder: str) -> Path:
    path = _absolute_path(value)
    if not path.is_relative_to(root / folder) or not path.is_file():
        _fail("资料库中的托管文件缺失或路径异常，请先修复原资料库；迁移未切换位置。", "LIBRARY_SOURCE_INVALID")
    return path.relative_to(root)


def _verify_database(connection: sqlite3.Connection) -> None:
    if [row[0] for row in connection.execute("PRAGMA integrity_check")] != ["ok"]:
        _fail("资料库完整性检查失败，未切换位置。", "LIBRARY_VERIFICATION_FAILED")
    if connection.execute("PRAGMA foreign_key_check").fetchone():
        _fail("资料库关联检查失败，未切换位置。", "LIBRARY_VERIFICATION_FAILED")


def _rewrite_managed_paths(connection: sqlite3.Connection, source: Path, target: Path) -> None:
    for table, column, folder in (("sources", "stored_path", "raw"), ("documents", "canonical_path", "parsed")):
        for identifier, value in connection.execute(f"SELECT id, {column} FROM {table}").fetchall():
            if table == "sources" and not value:
                continue  # First-party notes have a note:// original URI, not a raw file.
            relative = _managed_path(value, source, folder)
            if not (target / relative).is_file():
                _fail("迁移副本中缺少托管文件，未切换位置。", "LIBRARY_VERIFICATION_FAILED")
            connection.execute(f"UPDATE {table} SET {column}=? WHERE id=?", (str(target / relative), identifier))


def _persist_location(config: Path, target: Path, previous: bytes | None) -> None:
    _check_ancestors(config)
    if (config.read_bytes() if config.exists() else None) != previous:
        _fail("资料库位置配置在迁移期间发生变化，请重试。", "LIBRARY_LOCATION_CHANGED")
    temporary = config.with_name(f".{config.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps({"version": 1, "dataDir": str(target)}, ensure_ascii=False).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, config)  # Commit point: nothing fallible follows.
    except OSError as error:
        raise LibraryLocationError("LIBRARY_LOCATION_WRITE_FAILED", "无法保存新的资料库位置，仍使用原资料库。请检查配置目录权限或磁盘空间。") from error
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _discard_copy(target: Path, parent: Path) -> None:
    # Never remove a user-selected parent or the old library. This is exclusively
    # the unique directory created by this operation; suspicious changes stay put.
    try:
        _check_ancestors(target)
        if target.parent != parent or target == parent or not target.name.startswith("拾微资料库-"):
            return
        _inventory(target)
        shutil.rmtree(target)
    except OSError:
        pass
    except LibraryLocationError:
        pass


def _copy_file(source: Path, destination: Path, tick: Callable[[], None]) -> None:
    _check_ancestors(source)
    _check_ancestors(destination)
    with source.open("rb") as reader, destination.open("xb") as writer:
        for block in iter(lambda: reader.read(1024 * 1024), b""):
            writer.write(block)
            tick()
        writer.flush()
        os.fsync(writer.fileno())
    shutil.copystat(source, destination)


def relocate_library(importer: Importer, destination_parent: object, config: Path | None,
                     progress: Callable[[dict[str, Any]], None]) -> tuple[Importer, dict[str, Any]]:
    if config is None:
        _fail("当前应用未提供资料库位置配置，暂时无法更改位置。", "LIBRARY_LOCATION_UNAVAILABLE")
    config = _absolute_path(str(config))
    if config.is_relative_to(importer.data_dir):
        _fail("位置配置必须保存在资料库外部，未开始迁移。", "LIBRARY_LOCATION_INVALID")
    _destination_parent(destination_parent, importer.data_dir)
    try:
        config.parent.mkdir(parents=True, exist_ok=True)
        # Different open libraries may share one app configuration. Serialize
        # the external commit too, rejecting stale instances before any copy.
        with LibraryLock(config.parent, filename=f".{config.name}.lock"):
            selected = _read_location(config)
            if selected is not None and selected != importer.data_dir:
                _fail("资料库位置已被其他实例更改。请重新打开拾微后再迁移，避免覆盖新的位置。", "LIBRARY_LOCATION_CHANGED")
            return _relocate_locked(importer, destination_parent, config, progress)
    except OSError as error:
        raise LibraryLocationError("LIBRARY_MIGRATION_FAILED", "无法访问迁移目录，仍使用原资料库。请检查目录权限、磁盘连接和剩余空间。") from error


def _relocate_locked(importer: Importer, destination_parent: object, config: Path,
                     progress: Callable[[dict[str, Any]], None]) -> tuple[Importer, dict[str, Any]]:
    from shiwei_ai.ingestion import Importer

    source = importer.data_dir
    parent = _destination_parent(destination_parent, source)
    _check_ancestors(config)
    previous_config = config.read_bytes() if config.exists() else None
    progress({"phase": "preparing"})
    files, directories = _inventory(source)
    connection = importer.database.connection
    connection.commit()
    _verify_database(connection)
    db_size = connection.execute("PRAGMA page_count").fetchone()[0] * connection.execute("PRAGMA page_size").fetchone()[0]
    required_bytes = sum((source / path).stat().st_size for path in files) + 3 * db_size + 16 * 1024 * 1024
    if shutil.disk_usage(parent).free < required_bytes:
        _fail("目标磁盘剩余空间不足，未开始迁移；原资料库保持不变。", "LIBRARY_INSUFFICIENT_SPACE")

    target = parent / f"拾微资料库-{uuid4().hex[:10]}"
    target.mkdir(exist_ok=False)
    new_importer = None
    writer_lock = None
    committed = False
    try:
        # The Importer lifetime OS lease guards all managed files. This extra
        # SQLite lease also rejects legacy/concurrent writers during the copy.
        writer_lock = sqlite3.connect(importer.database.path, timeout=0)
        writer_lock.execute("BEGIN IMMEDIATE")
        snapshot_path = target / f".migration-snapshot-{uuid4().hex}.db"
        with closing(sqlite3.connect(snapshot_path)) as snapshot:
            connection.backup(snapshot, pages=256, progress=lambda *_: progress({"phase": "preparing"}))
            snapshot.execute("PRAGMA journal_mode=DELETE")
            _verify_database(snapshot)
        total = len(files) + 1
        progress({"phase": "copying", "completedFiles": 0, "totalFiles": total})
        for directory in directories:
            (target / directory).mkdir(parents=True, exist_ok=True)
        hashes = {}
        for completed, relative in enumerate([*files, Path("shiwei.db")], 1):
            original = snapshot_path if relative.name == "shiwei.db" and len(relative.parts) == 1 else source / relative
            _check_ancestors(original)
            expected = RawStore.sha256(original)
            _copy_file(original, target / relative, lambda: progress({"phase": "copying", "completedFiles": completed - 1, "totalFiles": total}))
            if RawStore.sha256(target / relative) != expected or RawStore.sha256(original) != expected:
                _fail("迁移文件校验失败或源文件发生变化，未切换位置。", "LIBRARY_VERIFICATION_FAILED")
            hashes[relative] = expected
            progress({"phase": "copying", "completedFiles": completed, "totalFiles": total})
        progress({"phase": "verifying", "completedFiles": 0, "totalFiles": total})
        if _inventory(source) != (files, directories):
            _fail("原资料库文件在迁移期间发生变化，未切换位置。", "LIBRARY_VERIFICATION_FAILED")
        for completed, relative in enumerate(files, 1):
            if RawStore.sha256(source / relative) != hashes[relative] or RawStore.sha256(target / relative) != hashes[relative]:
                _fail("迁移文件校验失败，未切换位置。", "LIBRARY_VERIFICATION_FAILED")
            progress({"phase": "verifying", "completedFiles": completed, "totalFiles": total})
        with closing(sqlite3.connect(target / "shiwei.db")) as copied:
            copied.execute("PRAGMA foreign_keys=ON")
            _rewrite_managed_paths(copied, source, target)
            copied.commit()
            _verify_database(copied)
        snapshot_path.unlink()
        progress({"phase": "verifying", "completedFiles": total, "totalFiles": total})
        new_importer = Importer(target)  # Acquire its own lease before committing.
        _verify_database(new_importer.database.connection)
        progress({"phase": "switching", "completedFiles": total, "totalFiles": total})
        result = {"dataDir": str(target), "previousDataDir": str(source), "copiedFiles": total, "retainedOriginal": True}
        _persist_location(config, target, previous_config)
        committed = True
        return new_importer, result
    except Exception as error:
        if new_importer is not None:
            new_importer.close()
        if not committed:
            _discard_copy(target, parent)
        if isinstance(error, LibraryLocationError):
            raise
        if isinstance(error, OSError) and error.errno == errno.ENOSPC:
            raise LibraryLocationError("LIBRARY_INSUFFICIENT_SPACE", "复制时磁盘空间不足，仍使用原资料库。") from error
        raise LibraryLocationError("LIBRARY_MIGRATION_FAILED", "迁移未完成，仍使用原资料库。请确认磁盘可写且没有其他程序占用资料库后重试。") from error
    finally:
        if writer_lock is not None:
            try:
                writer_lock.rollback()
                writer_lock.close()
            except sqlite3.Error:
                pass
