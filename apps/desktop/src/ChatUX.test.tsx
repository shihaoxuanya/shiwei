import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { useState } from "react";
import App, { ChatPage } from "./App";
import {
  askKnowledge,
  getConversation,
  listConversations,
  openOriginal,
} from "./lib/chat";
import { MessageContent } from "./components/chat/MessageContent";
import { SourcePanel } from "./components/chat/Sources";
import { createChatWorkspace } from "./stores/chat-workspace";
import { listSources } from "./lib/import";

vi.mock("./lib/chat", () => ({
  askKnowledge: vi.fn(),
  getConversation: vi.fn(),
  listConversations: vi.fn(),
  openOriginal: vi.fn(),
  revealOriginal: vi.fn(),
  cancelChat: vi.fn(),
}));
vi.mock("@tauri-apps/api/core", () => ({
  isTauri: () => true,
  invoke: vi.fn(),
}));
vi.mock("@tauri-apps/api/webview", () => ({
  getCurrentWebview: () => ({ onDragDropEvent: async () => () => {} }),
}));
vi.mock("./lib/import", async () => ({
  ...(await vi.importActual<object>("./lib/import")),
  listSources: vi.fn(),
}));
const ask = vi.mocked(askKnowledge);
const summary = {
  id: "old",
  title: "已有对话",
  createdAt: "2026-09-04",
  updatedAt: "2026-09-04",
};
const answer = {
  answer: "本次回复",
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
const input = () => screen.getByRole("textbox", { name: "消息" });
function type(text: string) {
  fireEvent.change(input(), { target: { value: text } });
}
function send(text: string) {
  type(text);
  fireEvent.keyDown(input(), { key: "Enter" });
}
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(listSources).mockResolvedValue([]);
  vi.mocked(listConversations).mockResolvedValue([summary]);
  vi.mocked(getConversation).mockResolvedValue({ ...summary, messages: [] });
});

it("supports multiline drafts and Shift+Enter without sending", async () => {
  render(<ChatPage onOpenSettings={() => {}} />);
  type("第一行\n第二行");
  fireEvent.keyDown(input(), { key: "Enter", shiftKey: true });
  expect(input()).toHaveValue("第一行\n第二行");
  expect(ask).not.toHaveBeenCalled();
  fireEvent.keyDown(input(), { key: "Enter", keyCode: 229 });
  expect(ask).not.toHaveBeenCalled();
});

it("does not silently truncate long pasted questions", () => {
  render(<ChatPage onOpenSettings={() => {}} />);
  const text = "文".repeat(2100);
  type(text);
  expect(input()).toHaveValue(text);
  expect(screen.getByRole("button", { name: "发送" })).toBeDisabled();
  fireEvent.keyDown(input(), { key: "Enter" });
  expect(ask).not.toHaveBeenCalled();
});

it("keeps distinct drafts when switching conversations", async () => {
  render(<ChatPage onOpenSettings={() => {}} />);
  type("新话题草稿");
  fireEvent.click(await screen.findByText("已有对话"));
  await waitFor(() => expect(getConversation).toHaveBeenCalled());
  type("旧对话草稿");
  fireEvent.click(screen.getByText("新话题草稿"));
  expect(input()).toHaveValue("新话题草稿");
  fireEvent.click(
    screen.getByText("已有对话", { selector: ".chat-history-title" }),
  );
  expect(input()).toHaveValue("旧对话草稿");
});

it("keeps draft and completed background reply across app navigation", async () => {
  const pending = deferred<typeof answer>();
  ask.mockReturnValue(pending.promise);
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "对话" }));
  send("留在这段对话");
  type("下一条草稿");
  fireEvent.click(screen.getByRole("button", { name: "资料" }));
  await act(async () => {
    ask.mock.calls[0][2]?.("流式内容");
    pending.resolve(answer);
  });
  fireEvent.click(screen.getByRole("button", { name: "对话" }));
  expect(input()).toHaveValue("下一条草稿");
  expect(screen.getByText("本次回复")).toBeVisible();
  expect(ask).toHaveBeenCalledOnce();
});

it("retains a background stream in its origin and blocks cross-session double send", async () => {
  const pending = deferred<typeof answer>();
  ask.mockReturnValue(pending.promise);
  render(<ChatPage onOpenSettings={() => {}} />);
  send("正在运行的问题");
  fireEvent.click(screen.getByRole("button", { name: "新建对话" }));
  type("第二段草稿");
  fireEvent.keyDown(input(), { key: "Enter" });
  expect(ask).toHaveBeenCalledOnce();
  expect(screen.getByText(/另一段对话正在回答/)).toBeVisible();
  await act(async () => {
    ask.mock.calls[0][2]?.("正在写");
  });
  expect(screen.queryByText("正在写")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("查看进度"));
  expect(screen.getByText("正在写")).toBeVisible();
  await act(async () => pending.resolve(answer));
  expect(screen.getByText("本次回复")).toBeVisible();
  fireEvent.click(screen.getByText("第二段草稿"));
  expect(input()).toHaveValue("第二段草稿");
});

it("keeps background errors in their original conversation", async () => {
  const pending = deferred<typeof answer>();
  ask.mockReturnValue(pending.promise);
  const store = createChatWorkspace();
  const key = store.getState().selected;
  const sending = store.getState().send("测试问题");
  store.getState().newConversation();
  pending.reject(new Error("模型超时"));
  await sending;
  expect(store.getState().sessions[key].error).toBe("模型超时");
  expect(
    store.getState().sessions[store.getState().selected].error,
  ).toBeUndefined();
  expect(store.getState().pending).toBeUndefined();
});

it("does not force scroll to the bottom while reading an active answer's history", async () => {
  const pending = deferred<typeof answer>();
  ask.mockReturnValue(pending.promise);
  render(<ChatPage onOpenSettings={() => {}} />);
  send("较长的问题");
  const area = screen.getByLabelText("对话消息");
  Object.defineProperty(area, "scrollHeight", { configurable: true, value: 1600 });
  Object.defineProperty(area, "clientHeight", { configurable: true, value: 350 });
  area.scrollTop = 200;
  fireEvent.scroll(area);
  await act(async () => { ask.mock.calls[0][2]?.("到达的新内容"); });
  expect(area.scrollTop).toBe(200);
  expect(screen.getByRole("button", { name: "回到最新消息" })).toBeVisible();
  await act(async () => pending.resolve(answer));
  expect(area.scrollTop).toBe(200);
  fireEvent.click(screen.getByRole("button", { name: "回到最新消息" }));
  expect(area.scrollTop).toBe(1600);
});

it("summarizes a file with one click without overwriting another draft", async () => {
  ask
    .mockResolvedValueOnce({
      ...answer,
      sourceMatches: [
        {
          sourceId: "s",
          filename: "简历.pdf",
          originalPath: "C:/resume.pdf",
          storedPath: "C:/copy.pdf",
          importedAt: "2026-09-04",
          status: "searchable",
        },
      ],
    })
    .mockResolvedValueOnce({ ...answer, answer: "总结完成" });
  render(<ChatPage onOpenSettings={() => {}} />);
  send("简历");
  await screen.findByText("总结这份资料");
  type("我另外想问的内容");
  fireEvent.click(screen.getByText("总结这份资料"));
  await screen.findByText("总结完成");
  expect(ask.mock.calls[1][0]).toBe("请总结 简历.pdf");
  expect(input()).toHaveValue("我另外想问的内容");
});

it("rejects stale history search responses and loads more than six histories", async () => {
  const old = deferred<(typeof summary)[]>();
  vi.mocked(listConversations)
    .mockReturnValueOnce(old.promise)
    .mockResolvedValueOnce(
      Array.from({ length: 12 }, (_, i) => ({ ...summary, id: String(i) })),
    );
  const store = createChatWorkspace();
  const stale = store.getState().refresh("旧关键词");
  await store.getState().refresh("新关键词");
  old.resolve([summary]);
  await stale;
  expect(store.getState().conversations).toHaveLength(12);
  expect(store.getState().historyQuery).toBe("新关键词");
});

it("searches history body through the worker, not only the loaded titles", async () => {
  render(<ChatPage onOpenSettings={() => {}} />);
  fireEvent.change(screen.getByRole("textbox", { name: "搜索历史对话" }), {
    target: { value: "磁盘容量" },
  });
  await waitFor(() =>
    expect(listConversations).toHaveBeenCalledWith("磁盘容量", 0),
  );
});

const citation = {
  citationId: "S1",
  documentId: "d",
  chunkId: "c",
  sourceFilename: "文件.pdf",
  sourcePath: "C:/original.pdf",
  storedPath: "C:/copy.pdf",
  pageNumber: 2,
  snippet: "真实引用文本",
};
it("closes source with Escape, restores focus and does not trap the composer", () => {
  function Harness() {
    const [open, setOpen] = useState(false);
    return (
      <>
        <button onClick={() => setOpen(true)}>查看</button>
        <textarea aria-label="继续提问" />
        {open && (
          <SourcePanel citation={citation} onClose={() => setOpen(false)} />
        )}
      </>
    );
  }
  render(<Harness />);
  const trigger = screen.getByText("查看");
  trigger.focus();
  fireEvent.click(trigger);
  expect(screen.getByRole("button", { name: "关闭来源详情" })).toHaveFocus();
  expect(screen.getByRole("textbox", { name: "继续提问" })).toBeEnabled();
  fireEvent.keyDown(document, { key: "Escape" });
  expect(screen.queryByLabelText("来源详情")).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it("shows source open failures and tries the managed copy", async () => {
  vi.mocked(openOriginal).mockRejectedValue(new Error("missing"));
  render(<SourcePanel citation={citation} onClose={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "打开文件" }));
  await screen.findByRole("alert");
  expect(openOriginal).toHaveBeenNthCalledWith(2, "C:/copy.pdf");
});

it("renders readable Markdown and copies code without executing remote content", async () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText },
  });
  const { container } = render(
    <MessageContent
      text={
        "## 测试标题\n\n- 项目 A\n- 项目 B\n\n| 列 | 内容 |\n| --- | --- |\n| 测试 | 值 |\n\n```sql\nSELECT 1;\n```\n\n<script>alert(1)</script>\n\n![远程图](https://tracking.invalid/pixel)\n\n[恶意](javascript:alert(1))"
      }
    />,
  );
  expect(screen.getByRole("heading", { name: "测试标题" })).toBeVisible();
  expect(screen.getAllByRole("listitem")).toHaveLength(2);
  expect(screen.getByRole("table")).toBeVisible();
  expect(container.querySelector("script, img, iframe, a[href] ")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "复制代码" }));
  await waitFor(() => expect(writeText).toHaveBeenCalledWith("SELECT 1;\n"));
});

it("offers history refresh failures separately from chat errors", async () => {
  vi.mocked(listConversations)
    .mockRejectedValueOnce(new Error("offline"))
    .mockResolvedValueOnce([summary]);
  render(<ChatPage onOpenSettings={() => {}} />);
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("历史对话暂时无法读取");
  fireEvent.click(within(alert).getByRole("button", { name: "重新加载" }));
  await screen.findByText("已有对话");
  expect(ask).not.toHaveBeenCalled();
});

it("only turns verified inline citations into source actions and retains trigger focus", async () => {
  ask.mockResolvedValue({
    ...answer,
    answer: "有依据的回答 [S1]。未验证 [S999]。代码 `示例 [S1]`。",
    citations: [citation],
  });
  render(<ChatPage onOpenSettings={() => {}} />);
  send("总结文件");
  const trigger = await screen.findByRole("button", {
    name: "[S1]",
  });
  expect(
    screen.queryByRole("button", { name: "[S999]" }),
  ).toBeNull();
  trigger.focus();
  fireEvent.click(trigger);
  expect(screen.getByRole("button", { name: "关闭来源详情" })).toHaveFocus();
  type("查看出处时写的草稿");
  fireEvent.keyDown(document, { key: "Escape" });
  expect(trigger).toHaveFocus();
  expect(trigger).toBeInTheDocument();
  expect(input()).toHaveValue("查看出处时写的草稿");
});

it("renders note citations as notes without file-system actions", () => {
  const openNote = vi.fn();
  render(
    <SourcePanel
      citation={{
        ...citation,
        citationId: "N1",
        sourceFilename: "TiDB会议记录",
        sourceType: "user_note",
        noteId: "note-1",
        noteCreatedAt: "2026-09-05T01:00:00Z",
        noteUpdatedAt: "2026-09-05T02:00:00Z",
        sourcePath: undefined,
        storedPath: undefined,
      }}
      onClose={() => {}}
      onOpenNote={openNote}
    />,
  );
  expect(screen.getByText("[N1] 笔记出处")).toBeVisible();
  expect(screen.queryByRole("button", { name: "定位文件" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "打开这条笔记" }));
  expect(openNote).toHaveBeenCalledWith("note-1");
});
