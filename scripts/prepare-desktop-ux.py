"""Seed an EMPTY, dedicated native desktop QA directory; never use a user library."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

from shiwei_ai.worker import WorkerServer


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--empty", action="store_true")
    options = parser.parse_args()
    root = options.root.resolve()
    if root.exists() and any(root.iterdir()):
        raise RuntimeError("Refusing a non-empty QA target; originals and existing libraries must be preserved")
    root.mkdir(parents=True, exist_ok=True)
    inputs = root / "inputs"
    inputs.mkdir()
    extra = inputs / "新增资料－正文搜索.txt"
    extra.write_text("合成验收：正文含水杉关键字和 SQL_ID 8v5abc，文件名没有这些词。", encoding="utf-8")
    server = WorkerServer(root / "library")
    try:
        if not options.empty:
            first = inputs / "恢复演练记录.txt"
            first.write_text("合成验收资料。Oracle恢复后PDB处于MOUNTED，需要OPEN。正文关键词：水杉，SQL_ID 8v5abc。", encoding="utf-8")
            long = inputs / "中文长文件名－生产环境数据库增量同步和恢复演练验收说明－仅为合成资料不得用于真实项目.txt"
            long.write_text("合成TiDB系统部署报告。该报告不是会议记录。正文关键字：银杏。", encoding="utf-8")
            broken = inputs / "损坏样本－处理失败.pdf"
            broken.write_bytes(b"Intentionally invalid PDF - synthetic QA only")
            server._import_paths({"paths": [str(first), str(long), str(broken)]})
            note = server._create_note({})["note"]
            server._update_note({"noteId": note["id"], "title": "2025年9月3日会议", "content": "本次会议围绕Oracle至TiDB数据库迁移方案展开。\n\n单次全量迁移预计耗时38~50天，增量追平需7~14天。至少需要两轮完整生产级全量迁移，分别用于性能测试和生产使用。测试环境的数据量需要确认。\n\n增量同步考虑TMS、OGG方案。"})
            second = server._create_note({})["note"]
            server._update_note({"noteId": second["id"], "title": "保存与滚动验收笔记", "content": "这是一条合成测试笔记，不读取个人资料。\n\n" + "\n\n".join(f"第 {index} 段：保存成功和可搜索是不同状态，离线关键词检索仍可用。" for index in range(1, 31))})
            with server._get_importer().database.transaction() as db:
                for index in range(8):
                    date = (datetime.now(timezone.utc) - timedelta(days=index // 3, minutes=index)).isoformat()
                    identifier = f"qa-history-{index}"
                    title = "合成记录：数据库恢复演练" if index == 0 else f"隔离验收话题 {index + 1}"
                    db.execute("INSERT INTO conversations(id,title,created_at,updated_at) VALUES(?,?,?,?)", (identifier, title, date, date))
                    db.execute("INSERT INTO messages(id,conversation_id,role,content,created_at) VALUES(?,?,'assistant',?,?)", (str(uuid4()), identifier, "这是一条合成历史回答，用于验证今天、昨天与更早分组。", date))
        manifest = {"synthetic": True, "dataDir": str(root / "library"), "configDir": str(root / "config"), "webviewDir": str(root / "webview"), "importFiles": [str(extra)], "inputDir": str(inputs), "empty": options.empty}
        (root / "qa-fixture.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(manifest, ensure_ascii=False), flush=True)
    finally:
        server._get_importer().close()


if __name__ == "__main__":
    main()
