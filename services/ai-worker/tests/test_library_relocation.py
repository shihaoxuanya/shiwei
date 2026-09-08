from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from shiwei_ai.ingestion import Importer
from shiwei_ai.storage import library_location as relocation
from shiwei_ai.storage.library_lock import LibraryLockError
from shiwei_ai.storage.raw_store import RawStore
from shiwei_ai.worker import WorkerServer


def rpc(server, method, params=None):
    return json.loads(server.process_line(json.dumps({"jsonrpc": "2.0", "protocol_version": "1.0", "id": "move-test", "method": method, "params": params or {}})))


@pytest.fixture
def library(tmp_path, monkeypatch):
    config = tmp_path / "config" / "library-location.json"
    monkeypatch.setenv("SHIWEI_LOCATION_CONFIG", str(config))
    old = tmp_path / "旧资料库"
    parent = tmp_path / "新的位置"
    parent.mkdir()
    original = tmp_path / "原始资料.txt"
    original.write_text(f"Oracle迁移会议2025年9月3日。保存路径示例{old}。", encoding="utf-8")
    events = []
    server = WorkerServer(old, event_sink=events.append)
    source = server._import_paths({"paths": [str(original)]})["imported"][0]
    note = server._create_note({})["note"]
    body = f"单次38~50天，增量7~14天。正文路径{old}不能改写。"
    server._update_note({"noteId": note["id"], "title": "2025年9月3日会议", "content": body})
    connection = server._get_importer().database.connection
    chunk = connection.execute("SELECT c.id,c.document_id,c.content FROM chunks c JOIN documents d ON d.id=c.document_id WHERE d.source_id=?", (source["sourceId"],)).fetchone()
    conversation = server._save_exchange(None, "会上说了什么？", "一次38～50天。[S1]", [{"citationId": "S1", "documentId": chunk["document_id"], "chunkId": chunk["id"], "sourceFilename": original.name, "snippet": chunk["content"]}], {"answerKind": "knowledge", "sourceMatches": [{"sourceId": source["sourceId"]}]})
    (old / "cache" / "空目录").mkdir()
    (old / "cache" / "opaque.bin").write_bytes(b"\x00\x01\xff")
    (old / "logs" / "synthetic.log").write_text("synthetic only")
    item = SimpleNamespace(server=server, config=config, old=old, parent=parent, note=note, body=body, original=original, source=source, conversation=conversation, events=events)
    try:
        yield item
    finally:
        server._shutdown({})


def test_relocate_preserves_truth_ids_fts_canonical_history_and_immutable_original(library):
    item = library
    connection = item.server._get_importer().database.connection
    truth_tables = ("notes", "sections", "chunks", "chunks_fts", "chunks_fts_trigram", "conversations", "messages", "citations", "message_sources", "jobs", "embedding_versions", "settings")
    before = {table: [tuple(row) for row in connection.execute(f"SELECT * FROM {table}")] for table in truth_tables}
    original_sources = [tuple(row) for row in connection.execute("SELECT original_path,stored_path FROM sources")]
    files, _ = relocation._inventory(item.old)
    hashes = {path: RawStore.sha256(item.old / path) for path in files}
    response = rpc(item.server, "relocate_library", {"destinationParent": str(item.parent)})
    assert "error" not in response, response
    result = response["result"]
    target = Path(result["dataDir"])
    assert target.parent == item.parent and target.name.startswith("拾微资料库-")
    assert result["previousDataDir"] == str(item.old) and result["retainedOriginal"] is True
    assert result["copiedFiles"] == len(files) + 1
    assert item.original.exists() and item.old.is_dir()
    assert (target / "cache" / "空目录").is_dir()
    assert {path: RawStore.sha256(target / path) for path in files} == hashes
    assert {path: RawStore.sha256(item.old / path) for path in files} == hashes
    current = item.server._get_importer().database.connection
    assert {table: [tuple(row) for row in current.execute(f"SELECT * FROM {table}")] for table in truth_tables} == before
    for table, column in (("sources", "stored_path"), ("documents", "canonical_path")):
        assert all(Path(row[0]).is_relative_to(target) for row in current.execute(f"SELECT {column} FROM {table}") if row[0])
    assert [row[0] for row in current.execute("SELECT original_path FROM sources")] == [row[0] for row in original_sources]
    assert item.server._get_note({"noteId": item.note["id"]})["note"]["content"] == item.body
    assert item.server._search_lexical({"query": "会议"})["hits"]
    history = item.server._get_conversation({"conversationId": item.conversation})["conversation"]
    assistant = history["messages"][-1]
    assert Path(assistant["citations"][0]["storedPath"]).is_relative_to(target)
    assert Path(assistant["sourceMatches"][0]["storedPath"]).is_relative_to(target)
    assert assistant["citations"][0]["sourcePath"] == str(item.original)
    assert json.loads(item.config.read_text(encoding="utf-8")) == {"version": 1, "dataDir": str(target)}
    phases = [event["data"]["phase"] for event in item.events if event["event"] == "library_migration_progress"]
    assert list(dict.fromkeys(phases)) == ["preparing", "copying", "verifying", "switching"]
    assert all(event["requestId"] == "move-test" for event in item.events if event["event"] == "library_migration_progress")
    # Old storage pointers remain old and the lease is released only after commit.
    retained = Importer(item.old)
    assert [tuple(row) for row in retained.database.connection.execute("SELECT original_path,stored_path FROM sources")] == original_sources
    retained.close()
    item.server._shutdown({})
    restarted = WorkerServer()
    assert restarted._worker_info({})["dataDir"] == str(target)
    assert restarted._get_note({"noteId": item.note["id"]})["note"]["content"] == item.body
    restarted._shutdown({})


@pytest.mark.parametrize("failure", ["copy", "hash", "integrity", "config", "open", "enospc"])
def test_migration_failure_keeps_original_live_and_location_unchanged(library, monkeypatch, failure):
    item = library
    item.config.parent.mkdir()
    previous = json.dumps({"version": 1, "dataDir": str(item.old)}).encode()
    item.config.write_bytes(previous)
    old_importer = item.server._get_importer()
    if failure in {"copy", "enospc"}:
        def fail_copy(*args):
            raise OSError(errno.ENOSPC if failure == "enospc" else errno.EACCES, "synthetic")
        monkeypatch.setattr(relocation, "_copy_file", fail_copy)
    elif failure == "hash":
        original_hash = RawStore.sha256
        monkeypatch.setattr(RawStore, "sha256", lambda path: "bad" if path.is_relative_to(item.parent) else original_hash(path))
    elif failure == "integrity":
        check = relocation._verify_database
        calls = 0
        def fail_check(connection):
            nonlocal calls
            calls += 1
            if calls > 2:
                raise relocation.LibraryLocationError("LIBRARY_VERIFICATION_FAILED", "synthetic")
            return check(connection)
        monkeypatch.setattr(relocation, "_verify_database", fail_check)
    elif failure == "config":
        monkeypatch.setattr(relocation, "_persist_location", lambda *args: (_ for _ in ()).throw(PermissionError("synthetic")))
    elif failure == "open":
        import shiwei_ai.ingestion
        monkeypatch.setattr(shiwei_ai.ingestion, "Importer", lambda *args: (_ for _ in ()).throw(PermissionError("synthetic")))
    response = rpc(item.server, "relocate_library", {"destinationParent": str(item.parent)})
    assert response["error"]["code"].startswith("LIBRARY_")
    assert item.server._get_importer() is old_importer
    assert item.config.read_bytes() == previous
    assert item.server._get_note({"noteId": item.note["id"]})["note"]["content"] == item.body
    saved = item.server._update_note({"noteId": item.note["id"], "content": "失败后仍然能够保存。"})
    assert saved["note"]["content"] == "失败后仍然能够保存。"
    assert item.original.exists()
    assert list(item.parent.iterdir()) == []
    with pytest.raises(LibraryLockError):
        Importer(item.old)


def test_disk_space_preflight_creates_no_target(library, monkeypatch):
    monkeypatch.setattr(relocation.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    response = rpc(library.server, "relocate_library", {"destinationParent": str(library.parent)})
    assert response["error"]["code"] == "LIBRARY_INSUFFICIENT_SPACE"
    assert list(library.parent.iterdir()) == []
    assert not library.config.exists()


@pytest.mark.parametrize("choice", ["current", "child", "missing", "file", "root", "relative", "empty", "none", "traversal"])
def test_rejects_unsafe_destinations_without_touching_existing_data(library, choice):
    item = library
    values = {"current": str(item.old), "child": str(item.old / "raw"), "missing": str(item.parent / "missing"), "file": str(item.original), "root": item.parent.anchor, "relative": "not/absolute", "empty": "", "none": None, "traversal": str(item.parent / ".." / "another")}
    response = rpc(item.server, "relocate_library", {"destinationParent": values[choice]})
    assert response["error"]["code"] == "LIBRARY_DESTINATION_INVALID"
    assert not item.config.exists()
    assert item.original.exists()
    assert list(item.parent.iterdir()) == []


@pytest.mark.parametrize("payload", [b"{broken", b"{}", b'{"version":99,"dataDir":"x"}'])
def test_corrupt_config_never_silently_creates_empty_default(tmp_path, monkeypatch, payload):
    config = tmp_path / "location.json"
    config.write_bytes(payload)
    fallback = tmp_path / "must-not-create"
    monkeypatch.setenv("SHIWEI_LOCATION_CONFIG", str(config))
    monkeypatch.setenv("SHIWEI_DATA_DIR", str(fallback))
    response = rpc(WorkerServer(), "worker_info")
    assert response["error"]["code"] == "LIBRARY_LOCATION_INVALID"
    assert not fallback.exists()


def test_missing_selected_library_is_explicit_but_explicit_test_data_dir_wins(tmp_path, monkeypatch):
    config = tmp_path / "location.json"
    missing = tmp_path / "removed-drive"
    config.write_text(json.dumps({"version": 1, "dataDir": str(missing)}))
    monkeypatch.setenv("SHIWEI_LOCATION_CONFIG", str(config))
    response = rpc(WorkerServer(), "worker_info")
    assert response["error"]["code"] == "LIBRARY_LOCATION_MISSING"
    assert not missing.exists()
    explicit = WorkerServer(tmp_path / "explicit")
    assert explicit._worker_info({})["dataDir"] == str(tmp_path / "explicit")
    explicit._shutdown({})


def test_missing_managed_copy_and_external_stored_path_reject_migration(library):
    item = library
    connection = item.server._get_importer().database.connection
    connection.execute("UPDATE sources SET stored_path=? WHERE id=?", (str(item.original), item.source["sourceId"]))
    connection.commit()
    response = rpc(item.server, "relocate_library", {"destinationParent": str(item.parent)})
    assert response["error"]["code"] == "LIBRARY_SOURCE_INVALID"
    assert item.original.exists() and not item.config.exists()


def test_os_lease_blocks_second_writer_and_releases_on_failed_initialization(tmp_path, monkeypatch):
    first = Importer(tmp_path / "data")
    first.database.create_job("active", "IMPORT", {})
    first.database.update_job("active", status="running", progress=0, current_step="synthetic")
    with pytest.raises(LibraryLockError):
        Importer(first.data_dir)
    assert first.database.connection.execute("SELECT status FROM jobs").fetchone()[0] == "running"
    first.close()
    original_indexer = __import__("shiwei_ai.ingestion.importer", fromlist=["DocumentIndexer"])
    with monkeypatch.context() as patch:
        patch.setattr(original_indexer, "DocumentIndexer", lambda *args: (_ for _ in ()).throw(RuntimeError("synthetic init failure")))
        with pytest.raises(RuntimeError, match="synthetic init failure"):
            Importer(tmp_path / "data")
    reopened = Importer(tmp_path / "data")
    reopened.close()


def test_symbolic_link_destination_is_rejected(library):
    link = library.parent / "link"
    try:
        link.symlink_to(library.old.parent, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not grant symlink creation; Windows junction coverage is separate")
    response = rpc(library.server, "relocate_library", {"destinationParent": str(link)})
    assert response["error"]["code"] == "LIBRARY_DESTINATION_INVALID"
    assert not library.config.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction behavior")
@pytest.mark.parametrize("inside_source", [False, True])
def test_windows_junction_is_rejected_without_following_target(library, inside_source):
    item = library
    link = (item.old / "cache" if inside_source else item.parent) / "junction"
    external = item.parent.parent / "junction-target"
    external.mkdir()
    sentinel = external / "keep.txt"
    sentinel.write_text("do not change", encoding="utf-8")
    command = "New-Item -ItemType Junction -Path '" + str(link).replace("'", "''") + "' -Target '" + str(external).replace("'", "''") + "' | Out-Null"
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command], check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        response = rpc(item.server, "relocate_library", {"destinationParent": str(item.parent if inside_source else link)})
        assert response["error"]["code"] == ("LIBRARY_UNSAFE_ENTRY" if inside_source else "LIBRARY_DESTINATION_INVALID")
        assert sentinel.read_text(encoding="utf-8") == "do not change"
        assert not item.config.exists()
    finally:
        link.rmdir()  # Remove this newly created junction only, not its target.


def test_corrupt_and_stale_config_cannot_be_overwritten_by_open_old_instance(library):
    item = library
    item.config.parent.mkdir()
    item.config.write_bytes(b"{broken")
    response = rpc(item.server, "relocate_library", {"destinationParent": str(item.parent)})
    assert response["error"]["code"] == "LIBRARY_LOCATION_INVALID"
    assert item.config.read_bytes() == b"{broken"
    other = Importer(item.parent / "another-library")
    other.close()
    stale = json.dumps({"version": 1, "dataDir": str(item.parent / "another-library")}).encode()
    item.config.write_bytes(stale)
    response = rpc(item.server, "relocate_library", {"destinationParent": str(item.parent)})
    assert response["error"]["code"] == "LIBRARY_LOCATION_CHANGED"
    assert item.config.read_bytes() == stale
    assert list(item.parent.iterdir()) == [item.parent / "another-library"]


def test_config_lease_prevents_parallel_location_commits(library):
    from shiwei_ai.storage.library_lock import LibraryLock
    item = library
    item.config.parent.mkdir()
    with LibraryLock(item.config.parent, filename=f".{item.config.name}.lock"):
        response = rpc(item.server, "relocate_library", {"destinationParent": str(item.parent)})
        assert response["error"]["code"] == "LIBRARY_IN_USE"
        assert not item.config.exists()
        assert not list(item.parent.iterdir())


def test_actual_foreign_key_violation_fails_verification_before_switch(library):
    item = library
    connection = item.server._get_importer().database.connection
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.execute("INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES ('orphan','missing','user','synthetic','2026-09-08')")
    connection.commit()
    connection.execute("PRAGMA foreign_keys=ON")
    response = rpc(item.server, "relocate_library", {"destinationParent": str(item.parent)})
    assert response["error"]["code"] == "LIBRARY_VERIFICATION_FAILED"
    assert not list(item.parent.iterdir()) and not item.config.exists()
    assert connection.execute("SELECT content FROM messages WHERE id='orphan'").fetchone()[0] == "synthetic"


def test_actual_copied_byte_corruption_is_detected(library, monkeypatch):
    copy = relocation._copy_file
    def corrupt(source, destination, tick):
        copy(source, destination, tick)
        with destination.open("ab") as stream:
            stream.write(b"corruption")
    monkeypatch.setattr(relocation, "_copy_file", corrupt)
    response = rpc(library.server, "relocate_library", {"destinationParent": str(library.parent)})
    assert response["error"]["code"] == "LIBRARY_VERIFICATION_FAILED"
    assert not list(library.parent.iterdir()) and not library.config.exists()
    assert library.server._get_note({"noteId": library.note["id"]})["note"]["content"] == library.body
