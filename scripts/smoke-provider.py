"""Opt-in live check: reads only Shiwei's chat credential, sends a benign short prompt.

No library is opened, no configuration is changed, and no key/model output is logged.
"""
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services/ai-worker"))


def chat_key():
    class Credential(ctypes.Structure):
        _fields_ = [("Flags", wintypes.DWORD), ("Type", wintypes.DWORD), ("TargetName", wintypes.LPWSTR), ("Comment", wintypes.LPWSTR), ("LastWritten", wintypes.FILETIME), ("CredentialBlobSize", wintypes.DWORD), ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)), ("Persist", wintypes.DWORD), ("AttributeCount", wintypes.DWORD), ("Attributes", ctypes.c_void_p), ("TargetAlias", wintypes.LPWSTR), ("UserName", wintypes.LPWSTR)]
    api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    api.CredReadW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.POINTER(Credential))]
    api.CredReadW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    pointer = ctypes.POINTER(Credential)()
    if not api.CredReadW("openai-compatible-api-key.com.shiwei.desktop", 1, 0, ctypes.byref(pointer)):
        raise RuntimeError("Shiwei chat credential is unavailable")
    try:
        value = pointer.contents
        return ctypes.string_at(value.CredentialBlob, value.CredentialBlobSize).decode("utf-16-le")
    finally:
        api.CredFree(pointer)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-benign-live-request", action="store_true", required=True)
    args = parser.parse_args()
    from shiwei_ai.models import ProviderConfig, create_gateway, ModelGatewayError
    config = json.loads((Path(os.environ["APPDATA"]) / "com.shiwei.desktop/provider.json").read_text(encoding="utf-8"))
    gateway = create_gateway(ProviderConfig(base_url=config["baseUrl"], chat_model=config["chatModel"], api_key=chat_key(), protocol=config.get("protocol", "openai_compatible"), embedding_mode="none", timeout_seconds=30))
    try:
        tokens = list(gateway.stream_chat([{"role": "user", "content": "连接测试，请只回复 OK。"}]))
        assert "".join(tokens).strip(), "empty response"
        print(json.dumps({"status": "passed", "streamReceived": True, "personalContentSent": False, "libraryOpened": False}))
    except ModelGatewayError as error:
        print(json.dumps({"status": "failed", "reason": str(error)}, ensure_ascii=False))
        sys.exit(1)
    finally:
        gateway.close()


if __name__ == "__main__":
    main()
