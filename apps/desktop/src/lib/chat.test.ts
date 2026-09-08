import { askKnowledge, cancelChat } from "./chat";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { libraryBusyReason, useLibraryLocationStore } from "./library-location";
vi.mock("@tauri-apps/api/core", () => ({ isTauri: () => true, invoke: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn() }));
beforeEach(() => {
  vi.clearAllMocks();
  useLibraryLocationStore.setState({ locked: false, activities: {} });
});

it("filters stream events by request id and always removes the listener", async () => {
  const token = vi.fn(), unlisten = vi.fn();
  vi.mocked(listen).mockImplementation(async (_event, handler) => {
    handler({ payload: { token: "wrong", requestId: "old" } } as never);
    handler({ payload: { token: "right", requestId: "current" } } as never);
    return unlisten;
  });
  vi.mocked(invoke).mockRejectedValue(new Error("offline"));
  await expect(askKnowledge("question", undefined, token, "current")).rejects.toThrow("offline");
  expect(token).toHaveBeenCalledExactlyOnceWith("right"); expect(unlisten).toHaveBeenCalledOnce();
  expect(invoke).toHaveBeenCalledWith("chat_ask", { query: "question", conversationId: undefined, requestId: "current" });
});

it("sends cancellation through its restricted request-id IPC", async () => {
  vi.mocked(invoke).mockResolvedValue(undefined);
  await cancelChat("current-id");
  expect(invoke).toHaveBeenCalledWith("chat_cancel", { requestId: "current-id" });
});

it("blocks new chat before invoking the worker while a library migration is active", async () => {
  useLibraryLocationStore.setState({ locked: true });
  await expect(askKnowledge("question")).rejects.toThrow("资料库正在迁移");
  expect(invoke).not.toHaveBeenCalled();
  expect(libraryBusyReason()).toBeUndefined();
});

it("registers actual generation as busy and releases it after success", async () => {
  let finish!: (answer: unknown) => void;
  vi.mocked(invoke).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  const request = askKnowledge("question");
  expect(libraryBusyReason()).toBe("正在生成回答");
  finish({ answer: "synthetic", citations: [], conversationId: "c" });
  await request;
  expect(libraryBusyReason()).toBeUndefined();
});

it("releases generation activity even when event subscription fails", async () => {
  vi.mocked(listen).mockRejectedValue(new Error("synthetic subscription failure"));
  await expect(askKnowledge("question", undefined, vi.fn())).rejects.toThrow("subscription failure");
  expect(invoke).not.toHaveBeenCalled();
  expect(libraryBusyReason()).toBeUndefined();
});
