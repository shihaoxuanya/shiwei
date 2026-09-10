from __future__ import annotations

import gzip
import io
import socket
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from shiwei_ai.ingestion import web_fetch as web


HTML = '<html><title>会议</title><article><p>Oracle到TiDB迁移会议，单次预计38~50天。</p></article></html>'.encode()


class Response:
    def __init__(self, data=HTML, *, status=200, headers=None):
        self.data = io.BytesIO(data)
        self.status = status
        self.headers = {"content-type": "text/html; charset=utf-8", **{k.lower(): v for k, v in (headers or {}).items()}}

    def getheader(self, name, default=None):
        return self.headers.get(name.lower(), default)

    def read1(self, size):
        return self.data.read(size)

    def close(self):
        pass


@pytest.fixture
def network(monkeypatch):
    state = {"responses": [Response()], "requests": [], "resolutions": []}

    def dns(host, port, **kwargs):
        state["resolutions"].append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.215.14", port))]

    class Connection:
        def __init__(self, host, port, addresses, deadline, *, secure):
            state["requests"].append({"host": host, "port": port, "addresses": addresses, "secure": secure})

        def request(self, method, target, headers):
            state["requests"][-1].update(method=method, target=target, headers=headers)

        def getresponse(self):
            return state["responses"].pop(0)

        def close(self):
            pass

    monkeypatch.setattr(web.socket, "getaddrinfo", dns)
    monkeypatch.setattr(web, "_PinnedConnection", Connection)
    return state


def test_normalize_host_unicode_fragment_and_default_port():
    assert web.normalize_public_url(" HTTPS://Example.COM:443/文章?a=1#top ") == "https://example.com/%E6%96%87%E7%AB%A0?a=1"
    assert web.normalize_public_url("https://例子.中国/").startswith("https://xn--")
    assert web.normalize_public_url("http://[2606:4700:4700::1111]:80/") == "http://[2606:4700:4700::1111]/"


@pytest.mark.parametrize("url", ["file:///private", "ftp://example.com/a", "https://user:secret@example.com/", "https://user@example.com/", "https://example.com\\@127.0.0.1/", "http://[fe80::1%25eth0]/", "https://example.com/\nheader", "javascript:alert(1)", "http://", "http://host:99999/", "http://host:0/", "http://exa%6dple.com/"])
def test_invalid_urls_rejected_without_echoing_url(url):
    with pytest.raises(web.WebImportError) as result:
        web.normalize_public_url(url)
    assert url not in str(result.value)
    assert "secret" not in str(result.value)


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "172.16.0.1", "192.168.1.1", "169.254.169.254", "0.0.0.0", "100.64.0.1", "224.0.0.1", "192.0.2.1", "::1", "::", "fe80::1", "fc00::1", "ff02::1", "::ffff:127.0.0.1", "2002:7f00:1::", "2001:db8::1", "168.63.129.16", "192.88.99.1", "64:ff9b::7f00:1", "64:ff9b:1::a00:1"])
def test_non_public_addresses_rejected(address):
    assert not web._is_public(address)


def test_success_no_cookie_proxy_credentials_or_referrer(network, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://secret:password@127.0.0.1:8888")
    snapshot = web.fetch_snapshot("https://example.com/article#anchor")
    assert snapshot.original_url == snapshot.final_url == "https://example.com/article"
    assert snapshot.raw_html == HTML and snapshot.document.title == "会议"
    assert len(snapshot.body_hash) == 64 and snapshot.captured_at.endswith("+00:00")
    assert network["requests"][0]["headers"] == {"User-Agent": "Shiwei-WebSnapshot/1.0", "Accept": "text/html,application/xhtml+xml", "Accept-Encoding": "identity", "Connection": "close"}


def test_mixed_public_private_dns_blocks_entire_destination(network, monkeypatch):
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443)) for ip in ["93.184.215.14", "127.0.0.1"]])
    with pytest.raises(web.WebImportError, match="内网"):
        web.fetch_snapshot("https://example.com/")
    assert not network["requests"]


def test_redirect_revalidates_dns_and_blocks_private(network, monkeypatch):
    network["responses"] = [Response(status=302, headers={"Location": "http://internal.test/private"})]
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda host, port, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1" if host == "internal.test" else "93.184.215.14", port))])
    with pytest.raises(web.WebImportError, match="内网"):
        web.fetch_snapshot("https://example.com/")
    assert len(network["requests"]) == 1


def test_five_redirects_allowed_six_rejected(network):
    redirects = [Response(status=302, headers={"Location": f"/page{i}"}) for i in range(5)]
    network["responses"] = redirects + [Response()]
    assert web.fetch_snapshot("https://example.com/").final_url == "https://example.com/page4"
    assert len(network["resolutions"]) == 6
    network["responses"] = [Response(status=302, headers={"Location": "/again"}) for _ in range(6)]
    with pytest.raises(web.WebImportError, match="重定向"):
        web.fetch_snapshot("https://example.com/")


@pytest.mark.parametrize("headers", [{"Content-Type": "application/pdf"}, {"Content-Disposition": "attachment; filename=test.html"}, {"Content-Type": "image/png"}])
def test_file_content_rejected(network, headers):
    network["responses"] = [Response(headers=headers)]
    with pytest.raises(web.WebImportError, match="文件"):
        web.fetch_snapshot("https://example.com/download")


def test_direct_file_url_rejected_before_dns(network):
    with pytest.raises(web.WebImportError, match="文件直链"):
        web.fetch_snapshot("https://example.com/test.pdf?download=1")
    assert network["resolutions"] == []


@pytest.mark.parametrize("encoding,compress", [("gzip", gzip.compress), ("deflate", zlib.compress)])
def test_stream_decompression_preserves_raw_html(network, encoding, compress):
    network["responses"] = [Response(compress(HTML), headers={"Content-Encoding": encoding})]
    assert web.fetch_snapshot("https://example.com/").raw_html == HTML


@pytest.mark.parametrize("compressed", [False, True])
def test_decompressed_limit_and_stream_limit(network, monkeypatch, compressed):
    monkeypatch.setattr(web, "MAX_HTML_BYTES", 128)
    data = b"a" * 129
    network["responses"] = [Response(gzip.compress(data) if compressed else data, headers={"Content-Encoding": "gzip" if compressed else "identity"})]
    with pytest.raises(web.WebImportError, match="20MB|过大"):
        web.fetch_snapshot("https://example.com/")


def test_exact_response_limit_allowed(network, monkeypatch):
    monkeypatch.setattr(web, "MAX_HTML_BYTES", len(HTML))
    network["responses"] = [Response(HTML, headers={"Content-Length": str(len(HTML))})]
    assert web.fetch_snapshot("https://example.com/").raw_html == HTML


def test_truncated_response_rejected(network):
    network["responses"] = [Response(HTML, headers={"Content-Length": str(len(HTML) + 3)})]
    with pytest.raises(web.WebImportError, match="不完整"):
        web.fetch_snapshot("https://example.com/")


def test_pinned_connection_uses_only_validated_ip_and_original_tls_hostname(monkeypatch):
    calls = []
    class Socket:
        def settimeout(self, value):
            pass
        def connect(self, address):
            calls.append(("connect", address))
        def close(self):
            pass
        def do_handshake(self):
            calls.append(("handshake",))
    class Context:
        def wrap_socket(self, current, *, server_hostname, do_handshake_on_connect):
            calls.append(("tls", server_hostname, do_handshake_on_connect))
            return current
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda *a, **k: pytest.fail("pinned connect must not resolve DNS again"))
    monkeypatch.setattr(web.socket, "socket", lambda *a, **k: Socket())
    monkeypatch.setattr(web.ssl, "create_default_context", lambda: Context())
    deadline = web._Deadline(1)
    try:
        connection = web._PinnedConnection("example.com", 443, [(socket.AF_INET, ("93.184.215.14", 443))], deadline, secure=True)
        connection.connect()
        assert calls == [("connect", ("93.184.215.14", 443)), ("tls", "example.com", False), ("handshake",)]
    finally:
        deadline.close()


def test_dns_cannot_outlive_global_request_budget(monkeypatch):
    done = threading.Event()
    monkeypatch.setattr(web, "FETCH_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(web.socket, "getaddrinfo", lambda *a, **k: done.wait(2))
    started = time.monotonic()
    try:
        with pytest.raises(web.WebImportError, match="超时"):
            web.fetch_snapshot("https://example.com/")
        assert time.monotonic() - started < 0.5
    finally:
        done.set()


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_actual_socket_slow_drip_is_stopped_by_total_deadline(monkeypatch, phase):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            try:
                if phase == "headers":
                    for char in b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n":
                        self.wfile.write(bytes([char]))
                        self.wfile.flush()
                        time.sleep(0.03)
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    for char in HTML:
                        self.wfile.write(bytes([char]))
                        self.wfile.flush()
                        time.sleep(0.03)
            except (OSError, ValueError):
                pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # Test-only transport seam: production resolver still forbids localhost.
    monkeypatch.setattr(web, "_resolve_public", lambda host, port, deadline: [(socket.AF_INET, server.server_address)])
    monkeypatch.setattr(web, "FETCH_TIMEOUT_SECONDS", 0.18)
    started = time.monotonic()
    try:
        with pytest.raises(web.WebImportError, match="超时"):
            web.fetch_snapshot("http://public.example/")
        assert time.monotonic() - started < 0.8
    finally:
        server.shutdown()
        server.server_close()


def test_errors_do_not_expose_url_or_query_secret(network, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("https://secret.example/?token=very-secret source body")
    monkeypatch.setattr(web, "_read_html", fail)
    with pytest.raises(web.WebImportError) as result:
        web.fetch_snapshot("https://secret.example/?token=very-secret")
    assert "secret" not in str(result.value) and "source body" not in str(result.value)
