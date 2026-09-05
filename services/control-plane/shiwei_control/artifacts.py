"""Read-only GitHub storage adapter; standard minisign verification, no signing capability."""
import base64
import hashlib
import time
from typing import Protocol
from urllib.parse import urlsplit
import httpx
from nacl.signing import VerifyKey
from .models import Artifact


class ReleaseStorageProvider(Protocol):
    def verify(self, artifact: Artifact) -> None: ...


def verify_minisign(public_key: str, signature: str, digest: bytes):
    public_lines = base64.b64decode(public_key, validate=True).decode().splitlines()
    signature_lines = base64.b64decode(signature, validate=True).decode().splitlines()
    key = base64.b64decode(public_lines[1], validate=True)
    sig = base64.b64decode(signature_lines[1], validate=True)
    if len(key) != 42 or len(sig) != 74 or key[:2] != b"Ed" or sig[:2] != b"ED" or key[2:10] != sig[2:10]:
        raise ValueError("更新签名格式或 key id 不匹配")
    if not signature_lines[2].startswith("trusted comment: "):
        raise ValueError("更新签名缺少可信注释")
    verifier = VerifyKey(key[10:])
    verifier.verify(digest, sig[10:])
    verifier.verify(sig[10:] + signature_lines[2][17:].encode(), base64.b64decode(signature_lines[3], validate=True))


class GitHubReleaseProvider:
    def __init__(self, repository: str, public_key: str):
        self.repository, self.public_key = repository, public_key

    def verify(self, artifact: Artifact):
        expected = f"https://github.com/{self.repository}/releases/download/v{artifact.version}/Shiwei_{artifact.version}_x64-setup.exe"
        if not self.repository or not self.public_key or artifact.artifact_url != expected:
            raise ValueError("安装包必须来自配置仓库的对应版本资源")
        sha, prehash = hashlib.sha256(), hashlib.blake2b(digest_size=64)
        received, prefix = 0, b""
        deadline = time.monotonic() + 840
        # GitHub's one redirect is restricted to its artifact CDN. Never follow arbitrary URLs.
        with httpx.Client(timeout=httpx.Timeout(30, connect=10), follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", expected) as initial:
                if initial.status_code not in (301, 302, 303, 307, 308):
                    raise ValueError("GitHub 安装包未准备好")
                target = initial.headers.get("location", "")
            url = urlsplit(target)
            if url.scheme != "https" or url.hostname != "release-assets.githubusercontent.com" or url.port not in (None, 443) or url.username or url.password:
                raise ValueError("安装包重定向不在允许范围")
            with client.stream("GET", target) as response:
                response.raise_for_status()
                for part in response.iter_bytes(256 * 1024):
                    if time.monotonic() > deadline:
                        raise ValueError("安装包验证超时")
                    received += len(part)
                    if received > artifact.size:
                        raise ValueError("安装包大小不匹配")
                    prefix = (prefix + part)[:2]
                    sha.update(part)
                    prehash.update(part)
        if received != artifact.size or prefix != b"MZ" or sha.hexdigest() != artifact.sha256:
            raise ValueError("安装包 SHA256 或大小不匹配")
        verify_minisign(self.public_key, artifact.signature, prehash.digest())
