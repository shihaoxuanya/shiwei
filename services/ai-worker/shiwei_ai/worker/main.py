from __future__ import annotations

import logging
import json
import sys
import os
from queue import Queue
from threading import Lock, Thread

from shiwei_ai.worker import WorkerServer
from shiwei_ai.schemas import RpcRequest


def configure_standard_streams() -> None:
    """Keep the Rust/Python JSONL boundary UTF-8 on every Windows locale."""
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(
                encoding="utf-8",
                errors="backslashreplace" if name == "stderr" else "strict",
            )


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def main() -> int:
    configure_standard_streams()
    configure_logging()
    output_lock = Lock()

    def emit_event(payload: dict[str, object]) -> None:
        with output_lock:
            sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            sys.stdout.flush()

    server = WorkerServer(event_sink=emit_event)
    requests: Queue[str | None] = Queue()

    def read_requests() -> None:
        # Only cancellation flags are touched here. Ordinary RPCs and all
        # database/index operations remain on the original, sole dispatcher.
        try:
            # os.read avoids holding Python's buffered-stdin lock in a daemon
            # while an explicit shutdown exits the process with the pipe open.
            def lines():
                pending = b""
                while data := os.read(sys.stdin.fileno(), 65536):
                    pending += data
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        yield line.decode("utf-8")
                if pending:
                    yield pending.decode("utf-8")

            for raw_line in lines():
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    request = RpcRequest.model_validate_json(line)
                    client_id = request.params.get("clientRequestId")
                    valid_id = isinstance(client_id, str) and 1 <= len(client_id) <= 128
                    if request.method == "chat" and valid_id:
                        server.prepare_chat(client_id)
                    elif request.method == "cancel_chat" and valid_id:
                        cancelled = server.cancel_chat(client_id)
                        emit_event({"jsonrpc": "2.0", "protocol_version": "1.0", "id": request.id, "result": {"cancelled": cancelled}})
                        continue
                except (ValueError, TypeError):
                    pass  # Existing dispatcher produces the normal RPC error.
                requests.put(line)
        finally:
            requests.put(None)

    Thread(target=read_requests, daemon=True, name="shiwei-rpc-input").start()
    while (line := requests.get()) is not None:
        response = server.process_line(line)
        with output_lock:
            sys.stdout.write(response + "\n")
            sys.stdout.flush()
        if server.should_stop:
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
