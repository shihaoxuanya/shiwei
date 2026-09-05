// Development-only entry; not referenced by index.html or the production build.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { mockIPC } from "@tauri-apps/api/mocks";
import { emit } from "@tauri-apps/api/event";
import { ChatPage } from "../src/App";
import "../src/styles.css";

Object.defineProperty(window, "isTauri", { value: true, configurable: true });

mockIPC(
  async (command, args) => {
    const methods: Record<string, string> = {
      chat_ask: "chat",
      list_conversations: "list_conversations",
      get_conversation: "get_conversation",
    };
    if (!(command in methods))
      throw new Error("此验收页面只支持对话；打开原件由桌面端验证。");
    const response = await fetch("http://127.0.0.1:1421/rpc", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ method: methods[command], params: args }),
    });
    const { result, events } = await response.json();
    for (const event of events)
      if (event.event === "chat_token")
        await emit("chat-token", { ...event.data, requestId: args?.requestId });
    return result;
  },
  { shouldMockEvents: true },
);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <div style={{ display: "flex", flexDirection: "column", height: "100vh" }}>
      <div className="shrink-0 border-b border-line px-10 py-3 text-xs text-muted">
        拾微 · 隔离验收：合成资料 + 真实 Python Worker；不读取个人资料
      </div>
      <div style={{ flex: 1, minHeight: 0 }}>
        <ChatPage onOpenSettings={() => {}} />
      </div>
    </div>
  </StrictMode>,
);
