"""Bounded public-web fetch with validated-IP pinning and no ambient credentials."""
from __future__ import annotations

import http.client
import ipaddress
import queue
import re
import socket
import ssl
import threading
import time
import zlib
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from shiwei_ai.ingestion.canonical import CanonicalDocument
from shiwei_ai.ingestion.web_extract import WebImportError, document_body_hash, extract_document


MAX_HTML_BYTES = 20 * 1024 * 1024
FETCH_TIMEOUT_SECONDS = 60.0
MAX_REDIRECTS = 5
_READ_SIZE = 64 * 1024
_FILE_EXTENSIONS = re.compile(r"\.(?:pdf|docx?|pptx?|xlsx?|csv|txt|md|jpe?g|png|webp|gif|bmp|svg|mp[34]|m4a|wav|zip|gz|7z|rar|exe|dmg)$", re.I)
_SPECIAL_NETWORKS = tuple(ipaddress.ip_network(value) for value in ("168.63.129.16/32", "192.88.99.0/24", "64:ff9b::/96", "64:ff9b:1::/48"))


@dataclass(frozen=True)
class WebSnapshot:
    original_url: str
    final_url: str
    captured_at: str
    raw_html: bytes
    document: CanonicalDocument
    body_hash: str


def normalize_public_url(url: str) -> str:
    """Syntactic normalization only; fetch additionally resolves every address."""
    if not isinstance(url, str) or len(url) > 8192:
        raise WebImportError("请输入有效的公开 HTTP 或 HTTPS 网页地址")
    value = url.strip()
    if not value or re.search(r"[\x00-\x20\x7f\\]", value):
        raise WebImportError("请输入有效的公开 HTTP 或 HTTPS 网页地址")
    try:
        parts = urlsplit(value)
        if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None or "@" in parts.netloc:
            raise ValueError
        host = parts.hostname.rstrip(".").lower()
        if "%" in host:
            raise ValueError
        if ":" in host:
            host = ipaddress.IPv6Address(host).compressed
            authority = f"[{host}]"
        else:
            host = host.encode("idna").decode("ascii")
            if not re.fullmatch(r"[a-z0-9.-]+", host) or ".." in host or not host:
                raise ValueError
            authority = host
        port = parts.port
        if port == 0:
            raise ValueError
        if port is not None and port != (443 if parts.scheme.lower() == "https" else 80):
            authority += f":{port}"
        path = quote(parts.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(parts.query, safe="/%?:@!$&'()*+,;=-._~")
        return urlunsplit((parts.scheme.lower(), authority, path, query, ""))
    except (ValueError, UnicodeError):
        raise WebImportError("请输入有效的公开 HTTP 或 HTTPS 网页地址，不支持带账号密码的链接") from None


class _Deadline:
    """Hard wall-clock budget also interrupts slow-drip HTTP headers/bodies."""
    def __init__(self, seconds: float) -> None:
        self.end = time.monotonic() + seconds
        self.socket: socket.socket | None = None
        self.expired = threading.Event()
        self.timer = threading.Timer(seconds, self._expire)
        self.timer.daemon = True
        self.timer.start()

    def _expire(self) -> None:
        self.expired.set()
        current = self.socket
        if current is not None:
            try:
                current.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                current.close()
            except OSError:
                pass

    def remaining(self) -> float:
        value = self.end - time.monotonic()
        if value <= 0 or self.expired.is_set():
            raise WebImportError("网页读取超时，请稍后重试或保存文件后导入")
        return value

    def attach(self, current: socket.socket) -> None:
        self.socket = current
        self.remaining()
        current.settimeout(self.remaining())

    def close(self) -> None:
        self.timer.cancel()
        self.socket = None


def _is_public(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
        if not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return False
        if any(ip in network for network in _SPECIAL_NETWORKS):
            return False
        if isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour or ip.teredo):
            return False
        return True
    except ValueError:
        return False


def _resolve_public(host: str, port: int, deadline: _Deadline) -> list[tuple[int, tuple]]:
    # OS DNS has no per-call timeout. A daemon lookup cannot hold the UI worker
    # past the global deadline, and its result is never used after expiry.
    result: queue.Queue = queue.Queue(maxsize=1)
    def resolve() -> None:
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except Exception:
            result.put(None)
    thread = threading.Thread(target=resolve, daemon=True, name="shiwei-public-web-dns")
    thread.start()
    try:
        answers = result.get(timeout=deadline.remaining())
    except queue.Empty:
        raise WebImportError("网页域名解析超时，请稍后重试") from None
    deadline.remaining()
    if not answers:
        raise WebImportError("无法解析网页域名，请检查网址与网络")
    if any(family not in {socket.AF_INET, socket.AF_INET6} or not _is_public(sockaddr[0]) for family, _, _, _, sockaddr in answers):
        raise WebImportError("只支持公开网页，不允许访问本机、内网或特殊网络地址")
    unique: list[tuple[int, tuple]] = []
    for family, _, _, _, sockaddr in answers:
        item = (family, sockaddr)
        if item not in unique:
            unique.append(item)
    return unique


class _PinnedConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, addresses: list[tuple[int, tuple]], deadline: _Deadline, *, secure: bool) -> None:
        super().__init__(host, port, timeout=deadline.remaining())
        self.addresses = addresses
        self.deadline = deadline
        self.secure = secure

    def connect(self) -> None:
        # Never call create_connection(host): that would perform a second DNS
        # lookup and reintroduce rebinding between validation and connection.
        for family, address in self.addresses:
            current = socket.socket(family, socket.SOCK_STREAM)
            try:
                self.deadline.attach(current)
                current.connect(address)
                if self.secure:
                    context = ssl.create_default_context()
                    # Manual handshake lets the hard deadline own the SSLSocket
                    # even while a peer slowly negotiates TLS.
                    current = context.wrap_socket(current, server_hostname=self.host, do_handshake_on_connect=False)
                    self.deadline.attach(current)
                    current.do_handshake()
                self.sock = current
                return
            except Exception:
                current.close()
                self.deadline.remaining()
        raise WebImportError("无法安全连接网页，请检查网络或稍后重试")


def _read_html(response: http.client.HTTPResponse, deadline: _Deadline) -> bytes:
    length = response.getheader("Content-Length", "")
    if length:
        try:
            if int(length) < 0 or int(length) > MAX_HTML_BYTES:
                raise WebImportError("网页超过 20MB，请保存为文件后导入")
        except ValueError:
            raise WebImportError("网页响应格式无效，请稍后重试") from None
    encoding = response.getheader("Content-Encoding", "identity").strip().lower()
    if encoding not in {"identity", "", "gzip", "deflate"}:
        raise WebImportError("网页压缩格式暂不支持，请保存为文件后导入")
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS if encoding == "gzip" else zlib.MAX_WBITS) if encoding in {"gzip", "deflate"} else None
    body = bytearray()
    wire_size = 0
    while True:
        deadline.remaining()
        data = response.read1(_READ_SIZE)
        deadline.remaining()
        if not data:
            break
        wire_size += len(data)
        if wire_size > MAX_HTML_BYTES:
            raise WebImportError("网页超过 20MB，请保存为文件后导入")
        decoded = decoder.decompress(data, MAX_HTML_BYTES - len(body) + 1) if decoder else data
        body.extend(decoded)
        if len(body) > MAX_HTML_BYTES:
            raise WebImportError("网页解压后超过 20MB，请保存为文件后导入")
        if decoder and (decoder.unused_data or decoder.unconsumed_tail):
            raise WebImportError("网页压缩内容过大或格式异常，请保存为文件后导入")
    if decoder:
        body.extend(decoder.flush(MAX_HTML_BYTES - len(body) + 1))
        if not decoder.eof or len(body) > MAX_HTML_BYTES:
            raise WebImportError("网页压缩内容不完整或过大，请保存为文件后导入")
    if length and wire_size != int(length):
        raise WebImportError("网页响应内容不完整，请稍后重试")
    return bytes(body)


def fetch_snapshot(url: str) -> WebSnapshot:
    original = normalize_public_url(url)
    current = original
    deadline = _Deadline(FETCH_TIMEOUT_SECONDS)
    connection: _PinnedConnection | None = None
    try:
        for redirect_count in range(MAX_REDIRECTS + 1):
            parts = urlsplit(current)
            if _FILE_EXTENSIONS.search(unquote(parts.path)):
                raise WebImportError("该链接是文件直链，请先下载文件，再使用添加文件导入")
            port = parts.port or (443 if parts.scheme == "https" else 80)
            addresses = _resolve_public(parts.hostname or "", port, deadline)
            connection = _PinnedConnection(parts.hostname or "", port, addresses, deadline, secure=parts.scheme == "https")
            # stdlib connection ignores proxies/cookies/.netrc. Only this fixed
            # header allowlist is sent; no Referer or previous redirect secrets.
            target = parts.path + ("?" + parts.query if parts.query else "")
            connection.request("GET", target, headers={"User-Agent": "Shiwei-WebSnapshot/1.0", "Accept": "text/html,application/xhtml+xml", "Accept-Encoding": "identity", "Connection": "close"})
            response = connection.getresponse()
            deadline.remaining()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location")
                if not location or redirect_count >= MAX_REDIRECTS:
                    raise WebImportError("网页重定向过多或地址无效，请使用最终网页地址重试")
                current = normalize_public_url(urljoin(current, location))
                response.close()
                connection.close()
                connection = None
                continue
            if response.status != 200:
                raise WebImportError("网页无法公开访问，请检查链接；登录或受限网页请粘贴正文后保存")
            content_type = response.getheader("Content-Type", "")
            mime = content_type.split(";", 1)[0].strip().lower()
            if mime not in {"text/html", "application/xhtml+xml"} or "attachment" in response.getheader("Content-Disposition", "").lower():
                raise WebImportError("该链接不是可保存的网页正文，文件请先下载后导入")
            raw_html = _read_html(response, deadline)
            document = extract_document(raw_html, content_type=content_type)
            deadline.remaining()
            return WebSnapshot(original, current, datetime.now(timezone.utc).isoformat(), raw_html, document, document_body_hash(document))
        raise WebImportError("网页重定向过多，请使用最终网页地址重试")
    except WebImportError:
        raise
    except Exception:
        if deadline.expired.is_set() or time.monotonic() >= deadline.end:
            raise WebImportError("网页读取超时，请稍后重试或保存文件后导入") from None
        raise WebImportError("网页读取失败，请检查网址、网络或证书；也可以保存文件后导入") from None
    finally:
        if connection is not None:
            connection.close()
        deadline.close()
