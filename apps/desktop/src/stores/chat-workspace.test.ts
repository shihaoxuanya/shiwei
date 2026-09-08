import { beforeEach, describe, expect, it, vi } from "vitest";
import { getConversation } from "../lib/chat";
import { createChatWorkspace } from "./chat-workspace";

vi.mock("../lib/chat", () => ({
  askKnowledge: vi.fn(), cancelChat: vi.fn(), deleteConversation: vi.fn(),
  getConversation: vi.fn(), listConversations: vi.fn(),
}));

describe("conversation sources after library relocation", () => {
  beforeEach(() => vi.resetAllMocks());

  function populated() {
    const store = createChatWorkspace();
    const key = store.getState().selected;
    const messages = [{
      id: "answer", role: "assistant" as const, content: "原文保留 D:/旧库，范围38～50天。[S1]",
      createdAt: "2026-09-08T00:00:00Z",
      citations: [{ citationId: "S1", documentId: "doc", chunkId: "chunk", sourceFilename: "说明.txt", sourcePath: "D:/原件.txt", storedPath: "D:/旧库/raw/file.txt", snippet: "原文" }],
      sourceMatches: [{ sourceId: "source", filename: "说明.txt", originalPath: "D:/原件.txt", storedPath: "D:/旧库/raw/file.txt", sourceType: "imported_file" as const, status: "searchable", mimeType: "text/plain", importedAt: "2026-09-08T00:00:00Z", size: 10 }],
    }];
    store.getState().patch(key, { conversationId: "conversation", title: "已保存会话", draft: "未发送草稿", scrollTop: 235, messages });
    const refreshed = messages.map(message => ({ ...message,
      citations: message.citations.map(citation => ({ ...citation, storedPath: "E:/新库/raw/file.txt" })),
      sourceMatches: message.sourceMatches.map(source => ({ ...source, storedPath: "E:/新库/raw/file.txt" })),
    }));
    const conversation = { id: "conversation", title: "已保存会话", createdAt: "2026-09-08", updatedAt: "2026-09-08", messages: refreshed };
    return { store, key, messages, conversation };
  }

  it("refreshes stored citation/card paths while preserving text, draft, scroll and selection", async () => {
    const { store, key, messages, conversation } = populated();
    vi.mocked(getConversation).mockResolvedValue(conversation);
    await store.getState().refreshAfterRelocation();
    const state = store.getState();
    expect(state.selected).toBe(key);
    expect(state.sessions[key]).toMatchObject({ draft: "未发送草稿", scrollTop: 235, loaded: true });
    expect(state.sessions[key].messages[0].content).toBe(messages[0].content);
    expect(state.sessions[key].messages[0].citations[0]).toMatchObject({ storedPath: "E:/新库/raw/file.txt", sourcePath: "D:/原件.txt" });
    expect(state.sessions[key].messages[0].sourceMatches?.[0].storedPath).toBe("E:/新库/raw/file.txt");
  });

  it("does not load or overwrite an in-flight conversation", async () => {
    const { store, key, messages } = populated();
    store.setState({ pending: { key, requestId: "pending", startedAt: 1 } });
    await store.getState().refreshAfterRelocation();
    expect(getConversation).not.toHaveBeenCalled();
    expect(store.getState().sessions[key].messages).toBe(messages);
  });

  it("does not overwrite messages changed while metadata is loading", async () => {
    const { store, key, messages, conversation } = populated();
    let resolve!: (value: typeof conversation) => void;
    vi.mocked(getConversation).mockReturnValue(new Promise(done => { resolve = done; }));
    const refresh = store.getState().refreshAfterRelocation();
    const newer = [...messages, { ...messages[0], id: "newer", content: "刚刚完成的回复" }];
    store.getState().patch(key, { messages: newer });
    resolve(conversation);
    await refresh;
    expect(store.getState().sessions[key].messages).toBe(newer);
  });

  it("keeps cached conversation and draft when refreshing fails", async () => {
    const { store, key, messages } = populated();
    vi.mocked(getConversation).mockRejectedValue(new Error("synthetic failure"));
    await expect(store.getState().refreshAfterRelocation()).rejects.toThrow("部分对话来源暂未刷新");
    expect(store.getState().sessions[key].messages).toBe(messages);
    expect(store.getState().sessions[key].draft).toBe("未发送草稿");
  });
});
