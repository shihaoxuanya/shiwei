"""Synthetic API timing; credential arrives on stdin, never printed or persisted.

Run with the worker Python. Optional --baseline reads the staged assistant for
comparison without modifying the worktree. Only the synthetic expression is sent.
"""
import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services/ai-worker"))
from shiwei_ai.chat.assistant import Assistant
from shiwei_ai.models.gateway import OpenAICompatibleGateway, ProviderConfig
from shiwei_ai.worker import WorkerServer

parser = argparse.ArgumentParser()
parser.add_argument("--model", default="glm-5.3")
parser.add_argument("--baseline", action="store_true")
args = parser.parse_args()
key = sys.stdin.readline().strip()
gateway = OpenAICompatibleGateway(ProviderConfig(
    base_url="https://open.bigmodel.cn/api/paas/v4", api_key=key,
    chat_model=args.model, embedding_mode="none", timeout_seconds=120,
))
del key
if args.baseline:
    code = subprocess.check_output(["git", "show", ":services/ai-worker/shiwei_ai/chat/assistant.py"], text=True, encoding="utf-8")
    namespace = {"__name__": "latency_baseline"}
    exec(compile(code, "staged_assistant", "exec"), namespace)
    Assistant = namespace["Assistant"]

calls = []
original_chat = gateway.chat
def chat(messages, **options):
    start = time.perf_counter()
    try:
        return original_chat(messages, **options)
    finally:
        calls.append({"stage": "non_stream", "seconds": round(time.perf_counter() - start, 3)})
gateway.chat = chat
with tempfile.TemporaryDirectory(prefix="shiwei-latency-") as directory:
    worker = WorkerServer(Path(directory))
    # Initialize the normal local database, using no real library or credentials.
    importer = worker._get_importer()
    from shiwei_ai.retrieval import HybridRetriever
    # Constructor is deliberately explicit: no online embedding is configured.
    retriever = HybridRetriever(importer.database)
    first = []
    start = time.perf_counter()
    try:
        answer = Assistant(retriever, gateway).ask("1+1", on_token=lambda _: first.append(time.perf_counter()) if not first else None)
        print(json.dumps({"baseline": args.baseline, "model": args.model,
            "seconds": round(time.perf_counter()-start, 3),
            "first_token_seconds": round(first[0]-start, 3) if first else None,
            "non_stream_calls": calls, "answer": answer["answer"],
            "kind": answer["answerKind"]}, ensure_ascii=True))
    except Exception as error:
        # Never print arbitrary response/request objects or credentials.
        print(json.dumps({"error_type": type(error).__name__, "seconds": round(time.perf_counter()-start, 3)}))
    finally:
        importer.close()
        gateway.close()
