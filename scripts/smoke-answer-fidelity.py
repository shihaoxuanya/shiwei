"""Packaged Worker fidelity over loopback HTTPS/SSE, using synthetic data only.

No user credentials/configuration or external model is used. A temporary test CA
is trusted only by this child Worker; production HTTPS verification stays on.
"""
import argparse
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from queue import Queue
import ssl
import subprocess
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', type=Path, required=True)
    parser.add_argument('--openssl', type=Path, default=Path(shutil.which('openssl') or 'C:/Program Files/Git/usr/bin/openssl.exe'))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='shiwei-fidelity-smoke-') as temporary:
        directory = Path(temporary)
        cert, key = directory / 'test-ca.pem', directory / 'test-key.pem'
        subprocess.run([str(args.openssl), 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=localhost', '-addext', 'subjectAltName=IP:127.0.0.1'], capture_output=True, check=True, timeout=30)
        state = {'answers': [], 'calls': 0, 'errors': []}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                state['calls'] += 1
                messages = payload['messages']
                combined = '\n'.join(m['content'] for m in messages)
                if 'Derived Inference' not in messages[0]['content']:
                    state['errors'].append('stale_generation_policy')
                if '38～50 天' not in combined or '7～14 天' not in combined:
                    state['errors'].append('range_missing_from_context')
                if 'source: TiDB系统部署报告' in combined:
                    state['errors'].append('deployment_report_sent_as_meeting_evidence')
                answer = state['answers'].pop(0) if state['answers'] else '未安排测试回答'
                self.send_response(200)
                if payload.get('stream'):
                    self.send_header('Content-Type', 'text/event-stream')
                    self.end_headers()
                    for char in answer:
                        self.wfile.write(('data: ' + json.dumps({'choices': [{'delta': {'content': char}}]}, ensure_ascii=False) + '\n\n').encode())
                    self.wfile.write(b'data: [DONE]\n\n')
                else:
                    body = json.dumps({'choices': [{'message': {'content': answer}}]}, ensure_ascii=False).encode()
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        env = {**os.environ, 'SHIWEI_DATA_DIR': str(directory / 'library'), 'SSL_CERT_FILE': str(cert), 'NO_PROXY': '127.0.0.1,localhost', 'HF_HUB_OFFLINE': '1', 'PYTHONIOENCODING': 'utf-8'}
        process = subprocess.Popen([str(args.worker.resolve())], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8', env=env)
        replies = Queue()
        def read_output():
            for line in process.stdout:
                replies.put(json.loads(line))
        threading.Thread(target=read_output, daemon=True).start()
        sequence = 0
        def rpc(method, params=None):
            nonlocal sequence
            sequence += 1
            process.stdin.write(json.dumps({'jsonrpc': '2.0', 'protocol_version': '1.0', 'id': str(sequence), 'method': method, 'params': params or {}}, ensure_ascii=False) + '\n')
            process.stdin.flush()
            events = []
            while True:
                response = replies.get(timeout=45)
                if 'event' in response:
                    if response['event'] == 'chat_token':
                        events.append(response['data']['token'])
                    continue
                assert 'error' not in response, response.get('error')
                return response['result'], ''.join(events)
        try:
            worker_version = rpc('ping')[0]['workerVersion']
            assert worker_version == (ROOT / 'VERSION').read_text().strip()
            note = rpc('create_note')[0]['note']
            raw = '本次会议讨论Oracle至TiDB迁移。单次全量迁移预计耗时38~50天，增量追平需7~14天。至少两轮完整生产级全量迁移。'
            rpc('update_note', {'noteId': note['id'], 'title': '2025年9月3日会议', 'content': raw})
            report = directory / 'TiDB系统部署报告.txt'
            report.write_text('TiDB迁移部署报告。会议内容请另见会议笔记，本报告记录服务器安装。', encoding='utf-8')
            rpc('import_paths', {'paths': [str(report)]})
            rpc('provider_configure', {'baseUrl': f'https://127.0.0.1:{server.server_port}/v1', 'chatModel': 'synthetic', 'apiKey': 'synthetic-only', 'embeddingMode': 'none'})
            state['answers'] = ['会议记录：单次38~50天[N1]，增量7~14天[N1]。']
            answer, tokens = rpc('chat', {'query': '之前TiDB迁移会上说了什么？', 'stream': True})
            assert answer['answer'] == tokens
            assert '38～50 天' in tokens and '7～14 天' in tokens and tokens.count('[N1]') == 1
            assert answer['citations'][0]['noteId'] == note['id']
            assert answer['citations'][0]['mentionedDates'] == ['2025-09-03']
            assert len(answer['sourceMatches']) == 1 and answer['sourceMatches'][0]['noteId'] == note['id']
            state['answers'] = ['会议说项目总工期76~100天。[N1]', '原文单次38~50天、至少两轮。[N1]\n\n如果两轮完全串行且每轮相同，简单计算76~100天；这不是原文直接给出的项目总工期。']
            corrected, tokens = rpc('chat', {'query': '两轮大概要多久？', 'stream': True})
            assert tokens == corrected['answer'] and '不是原文' in tokens and '76～100 天' in tokens
            assert '会议说项目总工期' not in tokens
            calls = state['calls']
            for query in ['我以前讨论过MongoDB迁移吗？', '我上次MongoDB迁移是什么时候？', 'MongoDB迁移会上说了什么？']:
                absent, tokens = rpc('chat', {'query': query, 'stream': True})
                assert absent['answerKind'] == 'not_found' and not absent['citations'] and not absent['sourceMatches']
                assert '没有找到可靠记录' in absent['answer']
            assert state['calls'] == calls == 3 and state['errors'] == []
            assert rpc('get_note', {'noteId': note['id']})[0]['note']['content'] == raw
            history = rpc('get_conversation', {'conversationId': answer['conversationId']})[0]
            assert history['conversation']['messages'][-1]['citations'][0]['mentionedDates'] == ['2025-09-03']
            print(json.dumps({'status': 'passed', 'workerVersion': worker_version, 'rangeContextAndSse': True, 'numericRepairBeforeEmission': True, 'citationGrouping': True, 'meetingEvidenceOnly': True, 'negativeCases': 3, 'noteDatesHistory': True, 'rawNoteUnchanged': True, 'gateway': 'loopback_https_synthetic', 'realCredentialsUsed': False}))
        finally:
            process.terminate()
            process.wait(timeout=15)
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    main()
