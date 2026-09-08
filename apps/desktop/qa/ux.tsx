// Development-only full application; real Python storage/routing, simulated model.
import { createRoot } from "react-dom/client";
import { mockIPC, mockWindows } from "@tauri-apps/api/mocks";
import { emit } from "@tauri-apps/api/event";
import App from "../src/App";
import "../src/styles.css";

Object.defineProperty(window, "isTauri", { value: true, configurable: true });
mockWindows("main");
const qaPort = new URLSearchParams(window.location.search).get("qaPort") === "1423" ? "1423" : "1422";
mockIPC(
  async (command, args) => {
    if (command.startsWith("plugin:webview|")) return;
    if (command === "analytics_track") return; // QA never reports production statistics.
    if (command === "update_check") return { status: "unconfigured" };
    const map: Record<string, string> = {
      worker_ping: "ping",
      worker_info: "worker_info",
      chat_ask: "chat",
      list_conversations: "list_conversations",
      get_conversation: "get_conversation",
      delete_conversation: "delete_conversation",
      list_notes: "list_notes",
      get_note: "get_note",
      create_note: "create_note",
      update_note: "update_note",
      index_note: "index_note",
      delete_note: "delete_note",
      list_sources: "list_sources",
      import_paths: "import_paths",
      delete_source: "delete_source",
      reindex_source: "reindex_source",
      search_lexical: "search_lexical",
      search_hybrid: "search_hybrid",
      provider_status: "provider_status",
      index_status: "index_status",
      chat_cancel: "cancel_chat",
      release_status: "qa_release_status",
      analytics_consent: "qa_analytics_consent",
      "plugin:dialog|open": "qa_fixture_paths",
    };
    if (!map[command])
      throw new Error("隔离预览不执行文件打开或配置修改，请在桌面应用中操作。");
    const params = { ...args };
    if (command === "chat_ask" || command === "chat_cancel") params.clientRequestId = args?.requestId;
    const response = await fetch(`http://127.0.0.1:${qaPort}/rpc`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ method: map[command], params }),
    });
    if (!response.ok || !response.body)
      throw new Error("隔离验收 Worker 无法连接");
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let end: number;
      while ((end = buffer.indexOf("\n")) >= 0) {
        const packet = JSON.parse(buffer.slice(0, end));
        buffer = buffer.slice(end + 1);
        if (packet.event === "chat_token")
          await emit("chat-token", {
            ...packet.data,
            requestId: args?.requestId,
          });
        else if (packet.event === "job_progress") await emit("import-progress", packet.data);
        else if (packet.error) throw new Error(packet.error.message);
        else if (packet.result) {
          if (command === "plugin:dialog|open") {
            const options = args?.options as { directory?: boolean } | undefined;
            return options?.directory ? packet.result.inputDir : packet.result.importFiles;
          }
          if (command === "search_lexical") return packet.result.hits;
          return command === "list_sources" ? packet.result.sources : packet.result;
        }
      }
    }
    throw new Error("隔离验收请求中断");
  },
  { shouldMockEvents: true },
);

createRoot(document.getElementById("root")!).render(
  <>
    <div
      style={{
        position: "fixed",
        bottom: 3,
        left: 12,
        zIndex: 50,
        fontSize: 9,
        color: "#726b5e",
        pointerEvents: "none",
      }}
    >
      隔离测试 · 合成资料 / 模拟模型
    </div>
    <App />
  </>,
);
