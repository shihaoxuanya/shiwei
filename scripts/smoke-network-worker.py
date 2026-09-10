"""Packaged JSONL worker + isolated trusted loopback TLS. No real key/data."""
import argparse
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
import os
from pathlib import Path
import queue
import ssl
import subprocess
import tempfile
import threading
import time

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", required=True, type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="shiwei-network-qa-") as tmp:
        root = Path(tmp)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
        now = datetime.now(timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                .public_key(key.public_key()).serial_number(x509.random_serial_number())
                .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
                .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
                .add_extension(x509.BasicConstraints(ca=True, path_length=None), True).sign(key, hashes.SHA256()))
        (root / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        (root / "key.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        ready, disconnected = threading.Event(), threading.Event()
        state = {"stall": True, "calls": 0}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                state["calls"] += 1
                if state["stall"]:
                    ready.set()
                    self.connection.settimeout(10)
                    try:
                        if not self.connection.recv(1):
                            disconnected.set()
                    except OSError:
                        pass
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                assert payload["stream"] is True, "Fast arithmetic must skip classifier"
                self.wfile.write(b'data: {"choices":[{"delta":{"content":"2"}}]}\n\ndata: [DONE]\n\n')
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(root / "cert.pem", root / "key.pem")
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        env = {**os.environ, "SHIWEI_DATA_DIR": str(root / "library"), "SSL_CERT_FILE": str(root / "cert.pem"), "SHIWEI_TELEMETRY_DISABLED": "1", "PYTHONIOENCODING": "utf-8", "NO_PROXY": "127.0.0.1"}
        child = subprocess.Popen([str(args.worker.resolve())], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        packets = queue.Queue()
        threading.Thread(target=lambda: [packets.put(json.loads(line)) for line in child.stdout], daemon=True).start()
        def send(identifier, method, params):
            child.stdin.write(json.dumps({"jsonrpc": "2.0", "protocol_version": "1.0", "id": identifier, "method": method, "params": params})+"\n")
            child.stdin.flush()
        def receive(identifier):
            while True:
                packet = packets.get(timeout=15)
                if packet.get("id") == identifier:
                    return packet
        try:
            send("config", "provider_configure", {"baseUrl": f"https://127.0.0.1:{server.server_port}", "apiKey": "synthetic", "chatModel": "synthetic", "embeddingMode": "none"})
            assert "result" in receive("config")
            send("chat", "chat", {"query": "1+1", "stream": True, "clientRequestId": "cancel-test"})
            assert ready.wait(15), "Worker never connected to TLS fixture"
            start = time.monotonic()
            send("cancel", "cancel_chat", {"clientRequestId": "cancel-test"})
            result = receive("chat")
            elapsed = time.monotonic()-start
            assert result["error"]["code"] == "CHAT_CANCELLED", result
            assert elapsed < 2 and disconnected.wait(2)
            send("history", "list_conversations", {})
            assert receive("history")["result"]["conversations"] == []
            state["stall"] = False
            for query in ["1＋1", "一加一等于几", "(1+1)*2"]:
                before = state["calls"]
                send("retry", "chat", {"query": query, "stream": True})
                answer = receive("retry")["result"]
                assert answer["answerKind"] == "general" and answer["answer"] == "2"
                assert state["calls"] == before+1
            print(json.dumps({"status": "passed", "cancelSeconds": round(elapsed, 3), "tlsSocketClosed": True, "cancelledHistoryEmpty": True, "arithmeticVariants": 3, "syntheticOnly": True}))
        finally:
            child.stdin.close()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
            server.shutdown()
            server.server_close()
            server_thread.join(2)


if __name__ == "__main__":
    main()
