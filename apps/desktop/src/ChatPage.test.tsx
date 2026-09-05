import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { StrictMode } from "react";
import App, { ChatPage, SourceMatchCard } from "./App";
import {
  askKnowledge,
  getConversation,
  listConversations,
  openOriginal,
  deleteConversation,
} from "./lib/chat";

vi.mock("./lib/chat", () => ({
  askKnowledge: vi.fn(),
  getConversation: vi.fn(),
  listConversations: vi.fn(),
  openOriginal: vi.fn(),
  revealOriginal: vi.fn(),
  deleteConversation: vi.fn(),
}));
vi.mock("@tauri-apps/api/core", () => ({
  isTauri: () => true,
  invoke: vi.fn(),
}));
vi.mock("@tauri-apps/api/webview", () => ({
  getCurrentWebview: () => ({ onDragDropEvent: async () => () => {} }),
}));
const ask = vi.mocked(askKnowledge);
const summary = {
  id: "old",
  title: "已有对话",
  createdAt: "2026-09-04",
  updatedAt: "2026-09-04",
};
const answer = {
  answer: "回答完成",
  conversationId: "new",
  citations: [],
  answerKind: "general" as const,
};
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((a, b) => {
    resolve = a;
    reject = b;
  });
  return { promise, resolve, reject };
}
function send(text = "测试问题") {
  const input = screen.getByPlaceholderText(/找一份资料/);
  fireEvent.change(input, { target: { value: text } });
  fireEvent.keyDown(input, { key: "Enter" });
  return input;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listConversations).mockResolvedValue([summary]);
  vi.mocked(getConversation).mockResolvedValue({
    ...summary,
    messages: [
      {
        id: "old-answer",
        role: "assistant",
        content: "旧会话内容",
        createdAt: "2026-09-04",
        citations: [],
      },
    ],
  });
});

it("shows the user message immediately and locks repeated Enter", async () => {
  const pending = deferred<typeof answer>();
  ask.mockReturnValue(pending.promise);
  render(<ChatPage onOpenSettings={() => {}} />);
  const input = send();
  fireEvent.change(input, { target: { value: "测试问题" } });
  fireEvent.keyDown(input, { key: "Enter" });
  expect(
    screen.getByText("测试问题", { selector: '[data-message-role="user"]' }),
  ).toBeInTheDocument();
  expect(ask).toHaveBeenCalledTimes(1);
  await act(async () => pending.resolve(answer));
  expect(
    screen.getAllByText("测试问题", { selector: '[data-message-role="user"]' }),
  ).toHaveLength(1);
  expect(screen.getByText("日常对话")).toBeInTheDocument();
});

it("does not send on an IME composition Enter", async () => {
  render(<ChatPage onOpenSettings={() => {}} />);
  await screen.findByText("已有对话");
  const input = screen.getByPlaceholderText(/找一份资料/);
  fireEvent.change(input, { target: { value: "中文" } });
  fireEvent.keyDown(input, { key: "Enter", isComposing: true });
  expect(ask).not.toHaveBeenCalled();
});

it("consumes a Home query once under StrictMode", async () => {
  ask.mockResolvedValue(answer);
  render(
    <StrictMode>
      <ChatPage initialQuery="来自首页的问题" onOpenSettings={() => {}} />
    </StrictMode>,
  );
  await screen.findByText("回答完成");
  expect(ask).toHaveBeenCalledTimes(1);
});

it("ignores late tokens and results after switching conversation", async () => {
  const pending = deferred<typeof answer>();
  ask.mockReturnValue(pending.promise);
  render(<ChatPage onOpenSettings={() => {}} />);
  await screen.findByText("已有对话");
  send();
  const token = ask.mock.calls[0][2]!;
  fireEvent.click(screen.getByText("已有对话"));
  await screen.findByText("旧会话内容");
  await act(async () => {
    token("串话内容");
    pending.resolve(answer);
  });
  expect(screen.queryByText("串话内容")).not.toBeInTheDocument();
  expect(screen.queryByText("回答完成")).not.toBeInTheDocument();
});

it("keeps a new conversation clean when an abandoned request finishes", async () => {
  const pending = deferred<typeof answer>();
  ask.mockReturnValue(pending.promise);
  render(<ChatPage onOpenSettings={() => {}} />);
  send();
  fireEvent.click(screen.getByRole("button", { name: "新建对话" }));
  await act(async () => pending.resolve(answer));
  expect(screen.queryByText("回答完成")).not.toBeInTheDocument();
});

it("does not report a successful answer as failed when history refresh fails", async () => {
  ask.mockResolvedValue(answer);
  vi.mocked(listConversations)
    .mockResolvedValueOnce([])
    .mockRejectedValue(new Error("刷新失败"));
  render(<ChatPage onOpenSettings={() => {}} />);
  send();
  await screen.findByText("回答完成");
  expect(screen.queryByText("刷新失败")).not.toBeInTheDocument();
});

it("shows the real model error and retry does not duplicate the user bubble", async () => {
  ask
    .mockRejectedValueOnce(new Error("模型连接失败，请检查网络"))
    .mockResolvedValueOnce(answer);
  render(<ChatPage onOpenSettings={() => {}} />);
  send();
  await screen.findByText("模型连接失败，请检查网络");
  fireEvent.click(screen.getByText("重试发送"));
  await screen.findByText("回答完成");
  expect(
    screen.getAllByText("测试问题", { selector: '[data-message-role="user"]' }),
  ).toHaveLength(1);
  expect(ask).toHaveBeenCalledTimes(2);
});

it("opens the real original path and falls back to the managed copy", async () => {
  vi.mocked(openOriginal)
    .mockRejectedValueOnce(new Error("original moved"))
    .mockResolvedValueOnce();
  const choose = vi.fn();
  render(
    <SourceMatchCard
      source={{
        sourceId: "s",
        filename: "YRR简历.pdf",
        originalPath: "C:/original.pdf",
        storedPath: "C:/library/copy.pdf",
        importedAt: "2026-09-04",
        status: "searchable",
      }}
      onChoose={choose}
    />,
  );
  fireEvent.click(screen.getByText("打开原件"));
  await waitFor(() =>
    expect(openOriginal).toHaveBeenNthCalledWith(2, "C:/library/copy.pdf"),
  );
  fireEvent.click(screen.getByText("总结这份资料"));
  expect(choose).toHaveBeenCalledOnce();
});

it("locks sending while an existing conversation is loading", async () => {
  const loading = deferred<Awaited<ReturnType<typeof getConversation>>>();
  vi.mocked(getConversation).mockReturnValue(loading.promise);
  render(<ChatPage onOpenSettings={() => {}} />);
  fireEvent.click(await screen.findByText("已有对话"));
  send("不应提前发送");
  expect(ask).not.toHaveBeenCalled();
  await act(async () => loading.resolve({ ...summary, messages: [] }));
  expect(screen.getByRole("button", { name: "发送" })).toBeEnabled();
});

it("keeps asking on the dedicated chat page instead of duplicating a Home composer", async () => {
  render(<App />);
  expect(screen.queryByText("问问拾微")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "对话" }));
  expect(screen.getByPlaceholderText(/找一份资料/)).toBeVisible();
  expect(ask).not.toHaveBeenCalled();
});

it("deletes a stored conversation after confirmation", async () => {
  vi.mocked(deleteConversation).mockResolvedValue();
  render(<ChatPage onOpenSettings={() => {}} />);
  await screen.findByText("已有对话");
  fireEvent.click(screen.getByRole("button", { name: "对话操作：已有对话" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "删除对话" }));
  expect(screen.getByRole("dialog")).toHaveTextContent("删除这段对话？");
  expect(screen.getByRole("dialog")).toHaveTextContent("不会删除你导入的资料或笔记");
  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  await screen.findByText("对话已删除");
  expect(deleteConversation).toHaveBeenCalledWith("old");
});

it("returns to a blank conversation when deleting the open conversation", async () => {
  vi.mocked(listConversations).mockResolvedValueOnce([summary]).mockResolvedValue([]);
  vi.mocked(deleteConversation).mockResolvedValue();
  render(<ChatPage onOpenSettings={() => {}} />);
  fireEvent.click(await screen.findByText("已有对话"));
  await screen.findByText("旧会话内容");
  fireEvent.click(screen.getByRole("button", { name: "对话操作：已有对话" }));
  fireEvent.click(screen.getByRole("menuitem", { name: "删除对话" }));
  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  expect(await screen.findByText("想起什么，都可以问问拾微")).toBeVisible();
});
