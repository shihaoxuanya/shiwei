"""Real loopback sockets: cancellation before headers/body and between SSE chunks."""
import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from shiwei_ai.chat.cancellation import CancellableGateway, ChatCancelled
from shiwei_ai.models.gateway import ProviderConfig, create_gateway


@pytest.mark.parametrize("protocol", ["openai_compatible", "anthropic"])
@pytest.mark.parametrize("stage", ["headers", "body", "sse"])
def test_stop_closes_stalled_socket_and_next_request_works(protocol, stage):
    ready, disconnected = threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            if disconnected.is_set():
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                value = {"choices": [{"message": {"content": "OK"}}]} if protocol == "openai_compatible" else {"content": [{"type": "text", "text": "OK"}]}
                self.wfile.write(json.dumps(value).encode())
                return
            if stage != "headers":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream" if stage == "sse" else "application/json")
                self.end_headers()
            if stage == "sse":
                packet = {"choices": [{"delta": {"content": "OK"}}]} if protocol == "openai_compatible" else {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "OK"}}
                self.wfile.write(("data: " + json.dumps(packet) + "\n\n").encode())
                self.wfile.flush()
            ready.set()
            self.connection.settimeout(4)
            try:
                if self.connection.recv(1) == b"":
                    disconnected.set()
            except (OSError, socket.timeout):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = ProviderConfig(base_url="https://example.invalid", api_key="synthetic", chat_model="synthetic", embedding_mode="none", protocol=protocol)
    # Local HTTP is test-only; production config still requires HTTPS.
    config = config.model_copy(update={"base_url": f"http://127.0.0.1:{server.server_port}"})
    original = create_gateway(config)
    stop = threading.Event()
    def check():
        if stop.is_set():
            raise ChatCancelled()
    gateway = CancellableGateway(original, check)
    outcome = []
    def invoke():
        try:
            if stage == "sse":
                list(gateway.stream_chat([{"role": "user", "content": "synthetic"}]))
            else:
                gateway.chat([{"role": "user", "content": "synthetic"}])
        except BaseException as error:
            outcome.append(error)
    caller = threading.Thread(target=invoke)
    try:
        caller.start()
        assert ready.wait(5)
        start = time.monotonic()
        stop.set()
        caller.join(2)
        assert not caller.is_alive()
        assert len(outcome) == 1 and isinstance(outcome[0], ChatCancelled)
        assert time.monotonic() - start < 1.5
        assert disconnected.wait(2), "Cancellation must close the network, not just abandon a thread"
        assert not original.client.is_closed, "Never close the shared provider client"
        stop.clear()
        assert gateway.chat([{"role": "user", "content": "synthetic retry"}]) == "OK"
        assert not any(t.name == "shiwei-http" and t.is_alive() for t in threading.enumerate())
    finally:
        stop.set()
        caller.join(5)
        original.close()
        server.shutdown()
        server.server_close()
        thread.join(2)
