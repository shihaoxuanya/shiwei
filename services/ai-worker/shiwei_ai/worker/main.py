from __future__ import annotations

import logging
import json
import sys

from shiwei_ai.worker import WorkerServer


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

    def emit_event(payload: dict[str, object]) -> None:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        sys.stdout.flush()

    server = WorkerServer(event_sink=emit_event)

    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        sys.stdout.write(server.process_line(line) + "\n")
        sys.stdout.flush()
        if server.should_stop:
            break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
