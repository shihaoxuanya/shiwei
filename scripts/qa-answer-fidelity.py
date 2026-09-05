"""Seed an isolated, synthetic desktop acceptance library (never the user's DB)."""
import argparse
import json
from pathlib import Path
import sys
import re
import sqlite3
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'services/ai-worker'))
from shiwei_ai.ingestion import Importer
from shiwei_ai.notes.service import NoteService
from shiwei_ai.retrieval import HybridRetriever

QUERIES = [
    '你记得我上次开会是什么时候吗？',
    '之前讨论Oracle迁移的会议是什么时候？',
    '之前TiDB迁移会上说了什么？',
    '那次会议说全量迁移要多久？',
    '增量追平多久？',
    '我之前有没有讨论过MongoDB迁移？',
]

BODY = '''本次会议围绕Oracle至TiDB数据迁移方案展开，明确了全量与增量迁移排期，探讨了应用适配测试环境的数据量需求及第三方增量同步工具选型。
单次全量迁移预计耗时38~50天，增量追平需7~14天。累计周期涉及5288张表、57.5TB数据量。单表最大规模10TB。
至少需要两轮完整生产级全量迁移，分别用于性能测试和生产使用，每轮耗时38~50天。
应用适配测试是否需要全量生产数据存在不确定性，需要与客户确认测试环境覆盖度。
增量同步讨论了TMS、OGG等方案，需根据合作方能力选择。'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    target = args.directory.resolve()
    qa_root = (ROOT / 'output/playwright').resolve()
    if not target.is_relative_to(qa_root):
        raise SystemExit('Only output/playwright synthetic QA libraries are allowed')
    if args.verify:
        verify(target)
        return
    if target.exists():
        raise SystemExit('Use a new directory inside output/playwright; refusing existing data')
    importer = Importer(target)
    notes = NoteService(importer.database, target / 'parsed')
    note = notes.create()
    notes.update(note['id'], '2025年9月3日会议', BODY)
    fixtures = target / 'synthetic-originals'
    fixtures.mkdir()
    for filename, body in (
        ('TiDB系统部署报告.txt', 'TiDB迁移部署报告：安装TiDB数据库、设置服务器参数与部署拓扑。部署记录不包含会议决策。'),
        ('OCP考试资料.txt', 'Oracle OCP考试预约截止日期2026年11月28日。考试订单有效期与补考信息。'),
    ):
        path = fixtures / filename
        path.write_text(body, encoding='utf-8')
        importer.import_paths([str(path)])
    assert notes.get(note['id'])['content'] == BODY
    importer.close()
    print(json.dumps({'syntheticOnly': True, 'noteId': note['id'], 'dataDir': str(target)}, ensure_ascii=False))


def verify(target):
    connection = sqlite3.connect((target / 'shiwei.db').as_uri() + '?mode=ro', uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA query_only=ON')
    note = connection.execute('SELECT * FROM notes').fetchone()
    assert note['content'] == BODY, 'Source note was changed'
    messages = connection.execute('SELECT * FROM messages ORDER BY created_at, rowid').fetchall()
    retriever = HybridRetriever(SimpleNamespace(connection=connection), debug=True)
    cases = []
    for query in QUERIES:
        pairs = [(row, messages[i+1]) for i, row in enumerate(messages[:-1]) if row['role'] == 'user' and row['content'] == query and messages[i+1]['role'] == 'assistant']
        if not pairs:
            cases.append({'query': query, 'passed': False, 'reason': 'not_executed'})
            continue
        answer = pairs[-1][1]
        text = re.sub(r'\s|\*', '', answer['content'])
        citations = [dict(row) for row in connection.execute('SELECT c.citation_id, n.id AS note_id FROM citations c JOIN documents d ON d.id=c.document_id LEFT JOIN notes n ON n.source_id=d.source_id WHERE c.message_id=?', (answer['id'],))]
        correct_citations = bool(citations) and all(c['note_id'] == note['id'] for c in citations)
        passed = correct_citations and '3850天' not in text and '714天' not in text and '部署报告' not in text
        if query in QUERIES[:2]:
            passed &= '2025年9月3日' in text
        if query == QUERIES[3]:
            passed &= '38～50天' in text
        if query == QUERIES[4]:
            passed &= '7～14天' in text
        if query == QUERIES[5]:
            passed = '没有找到可靠记录' in text and not citations and answer['answer_kind'] == 'not_found'
        cases.append({'query': query, 'answer': answer['content'], 'answerKind': answer['answer_kind'], 'citations': citations, 'passed': bool(passed), 'retrievalTrace': retriever.retrieve(query)['debugTrace']})
    report = {'syntheticOnly': True, 'rawNoteUnchanged': True, 'cases': cases, 'passed': all(c['passed'] for c in cases)}
    (target.parent / 'fidelity-live-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    connection.close()
    print(json.dumps({**report, 'cases': [{k: v for k, v in c.items() if k != 'retrievalTrace'} for c in cases]}, ensure_ascii=False, indent=2))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
