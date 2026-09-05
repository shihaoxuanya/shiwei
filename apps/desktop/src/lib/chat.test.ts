import { askKnowledge } from "./chat";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
vi.mock("@tauri-apps/api/core", () => ({ isTauri: () => true, invoke: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn() }));

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
