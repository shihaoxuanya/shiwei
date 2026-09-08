import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createRef } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { NotesPage, type NotesPageHandle } from "./NotesPage";
import { createNote, deleteNote, getNote, indexNote, listNotes, updateNote } from "../../lib/notes";
import { useLibraryLocationStore } from "../../lib/library-location";

const native = vi.hoisted(() => ({
  close: undefined as undefined | ((event: { preventDefault: () => void }) => Promise<void>),
  destroy: vi.fn(),
  unlisten: vi.fn(),
}));
vi.mock("@tauri-apps/api/core", () => ({ isTauri: vi.fn(() => false) }));
vi.mock("@tauri-apps/api/window", () => ({ getCurrentWindow: () => ({
  onCloseRequested: vi.fn(async (handler) => { native.close = handler; return native.unlisten; }),
  destroy: native.destroy,
}) }));

vi.mock("../../lib/notes", () => ({
  listNotes: vi.fn(),
  getNote: vi.fn(),
  createNote: vi.fn(),
  updateNote: vi.fn(),
  deleteNote: vi.fn(),
  indexNote: vi.fn(),
}));

const note = {
  id: "note-1",
  sourceId: "source-1",
  title: "",
  content: "",
  displayTitle: "无标题笔记",
  createdAt: "2026-09-05T01:00:00Z",
  updatedAt: "2026-09-05T01:00:00Z",
  retrieval: { revision: "2026-09-05T01:00:00Z", keyword: "empty" as const, semantic: "disabled" as const },
};

beforeEach(() => {
  vi.clearAllMocks();
  useLibraryLocationStore.setState({ locked: false, progress: null, revision: 0, activities: {} });
  localStorage.clear();
  native.close = undefined;
  native.destroy.mockResolvedValue(undefined);
  vi.mocked(isTauri).mockReturnValue(false);
  vi.mocked(listNotes).mockResolvedValue([]);
  vi.mocked(createNote).mockResolvedValue(note);
  vi.mocked(updateNote).mockImplementation(async (id, title, content) => ({
    ...note,
    id,
    title,
    content,
    displayTitle: title || content.split("\n")[0] || "无标题笔记",
    updatedAt: "2026-09-05T01:01:00Z",
    retrieval: { revision: "2026-09-05T01:01:00Z", keyword: "ready", semantic: "disabled" },
  }));
  vi.mocked(deleteNote).mockResolvedValue();
});

afterEach(() => {
  vi.useRealTimers();
});

it("does not destroy the window while library migration is locked even with an already-saved note", async () => {
  vi.mocked(isTauri).mockReturnValue(true);
  render(<NotesPage newRequestKey="migration-note" />);
  await screen.findByRole("textbox", { name: "笔记正文" });
  act(() => useLibraryLocationStore.setState({ locked: true }));
  const preventDefault = vi.fn();
  await act(async () => { await native.close!({ preventDefault }); });
  expect(preventDefault).toHaveBeenCalledOnce();
  expect(native.destroy).not.toHaveBeenCalled();
  act(() => useLibraryLocationStore.setState({ locked: false }));
  await act(async () => { await native.close!({ preventDefault }); });
  expect(native.destroy).toHaveBeenCalledOnce();
});

it("reloads the selected note after relocation without creating or rewriting it", async () => {
  vi.mocked(listNotes).mockResolvedValue([{ ...note, title: "保留的笔记", displayTitle: "保留的笔记", content: "同一资料库" }]);
  vi.mocked(getNote).mockResolvedValue({ ...note, title: "保留的笔记", displayTitle: "保留的笔记", content: "同一资料库" });
  const { rerender } = render(<NotesPage libraryRevision={0} />);
  await screen.findByDisplayValue("同一资料库");
  rerender(<NotesPage libraryRevision={1} />);
  await waitFor(() => expect(getNote).toHaveBeenCalledWith("note-1"));
  expect(screen.getByRole("textbox", { name: "笔记正文" })).toHaveValue("同一资料库");
  expect(createNote).not.toHaveBeenCalled(); expect(updateNote).not.toHaveBeenCalled();
});

it("creates a blank note and auto-saves the combined title and body", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  render(<NotesPage newRequestKey="new-1" />);
  await act(async () => { await vi.advanceTimersByTimeAsync(0); });
  const title = screen.getByRole("textbox", { name: "笔记标题" });
  fireEvent.change(title, { target: { value: "TiDB会议记录" } });
  fireEvent.change(screen.getByRole("textbox", { name: "笔记正文" }), {
    target: { value: "黄总最终确认需要四套环境。" },
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(749); });
  expect(updateNote).not.toHaveBeenCalled();
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(updateNote).toHaveBeenCalledExactlyOnceWith(
    "note-1",
    "TiDB会议记录",
    "黄总最终确认需要四套环境。",
  );
  expect(screen.getByText("已保存，可按关键词查找")).toBeVisible();
  expect(indexNote).not.toHaveBeenCalled();
});

it("restores the last opened note and scroll without duplicating note content in preferences", async () => {
  const second = { ...note, id: "note-2", title: "继续这里", content: "很长的正文", displayTitle: "继续这里" };
  vi.mocked(listNotes).mockResolvedValue([note, second]);
  localStorage.setItem("shiwei.notes.last-open", second.id);
  localStorage.setItem("shiwei.notes.scroll", JSON.stringify({ "note-2": { body: 240, editor: 12 } }));
  render(<NotesPage />);
  expect(await screen.findByRole("textbox", { name: "笔记标题" })).toHaveValue("继续这里");
  await waitFor(() => expect(screen.getByRole("textbox", { name: "笔记正文" }).scrollTop).toBe(240));
  expect(localStorage.getItem("shiwei.notes.scroll")).not.toContain("很长的正文");
});

it("restores stored scroll after commit even when a frame runs before the note editor mounts", async () => {
  const second = { ...note, id: "note-2", title: "继续这里", content: "很长的正文", displayTitle: "继续这里" };
  const frames: FrameRequestCallback[] = [];
  const frame = vi.spyOn(window, "requestAnimationFrame").mockImplementation((callback) => frames.push(callback));
  let finish!: (value: typeof note[]) => void;
  vi.mocked(listNotes).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  localStorage.setItem("shiwei.notes.last-open", second.id);
  localStorage.setItem("shiwei.notes.scroll", JSON.stringify({ "note-2": { body: 240, editor: 12 } }));
  const view = render(<NotesPage />);
  try {
    await waitFor(() => expect(listNotes).toHaveBeenCalledOnce());
    await act(async () => {
      finish([note, second]);
      await Promise.resolve();
      // The list promise resolved, but React has not committed its editor yet.
      expect(screen.queryByRole("textbox", { name: "笔记正文" })).not.toBeInTheDocument();
      for (const callback of frames.splice(0)) callback(performance.now());
    });
    expect(screen.getByRole("textbox", { name: "笔记标题" })).toHaveValue("继续这里");
    expect(screen.getByRole("textbox", { name: "笔记正文" }).scrollTop).toBe(240);
    expect(screen.getByRole("main").scrollTop).toBe(12);
  } finally {
    view.unmount();
    frame.mockRestore();
  }
});

it("falls back to an existing note when the previously opened note was deleted", async () => {
  localStorage.setItem("shiwei.notes.last-open", "deleted-note");
  vi.mocked(listNotes).mockResolvedValue([{ ...note, title: "保留记录", displayTitle: "保留记录" }]);
  render(<NotesPage />);
  expect(await screen.findByRole("textbox", { name: "笔记标题" })).toHaveValue("保留记录");
});

it("restores scroll when the mounted notes page becomes visible again", async () => {
  vi.mocked(listNotes).mockResolvedValue([note]);
  const view = render(<NotesPage active />);
  const body = await screen.findByRole("textbox", { name: "笔记正文" });
  fireEvent.scroll(body, { target: { scrollTop: 185 } });
  view.rerender(<NotesPage active={false} />);
  fireEvent.scroll(body, { target: { scrollTop: 0 } });
  view.rerender(<NotesPage active />);
  await waitFor(() => expect(body.scrollTop).toBe(185));
});

it("reuses only the empty draft created in this session, preserving historical blank notes", async () => {
  vi.mocked(listNotes).mockResolvedValue([{ ...note, id: "old-blank" }]);
  render(<NotesPage />);
  await screen.findByRole("textbox", { name: "笔记正文" });
  fireEvent.click(screen.getByRole("button", { name: "记一下" }));
  await waitFor(() => expect(createNote).toHaveBeenCalledTimes(1));
  await waitFor(() => expect(screen.getAllByRole("button", { name: /无标题笔记/ })).toHaveLength(2));
  fireEvent.click(screen.getByRole("button", { name: "记一下" }));
  fireEvent.click(screen.getByRole("button", { name: "记一下" }));
  await waitFor(() => expect(screen.getByRole("button", { name: "记一下" })).toBeEnabled());
  expect(createNote).toHaveBeenCalledTimes(1);
  expect(deleteNote).not.toHaveBeenCalled();
});

it("retains failed edits, blocks switching and retries the exact text", async () => {
  vi.mocked(listNotes).mockResolvedValue([{ ...note, title: "当前记录", displayTitle: "当前记录" }, { ...note, id: "note-2", title: "另一个", displayTitle: "另一个" }]);
  vi.mocked(updateNote).mockRejectedValueOnce(new Error("磁盘暂时不可写"));
  render(<NotesPage />);
  fireEvent.change(await screen.findByRole("textbox", { name: "笔记正文" }), { target: { value: "不能丢失的输入" } });
  fireEvent.click(screen.getByRole("button", { name: /另一个/ }));
  expect(await screen.findByText(/磁盘暂时不可写/)).toBeVisible();
  expect(screen.getByRole("textbox", { name: "笔记标题" })).toHaveValue("当前记录");
  expect(screen.getByRole("textbox", { name: "笔记正文" })).toHaveValue("不能丢失的输入");
  fireEvent.click(screen.getByRole("button", { name: "重试保存" }));
  expect(await screen.findByText("已保存，可按关键词查找")).toBeVisible();
  expect(updateNote).toHaveBeenLastCalledWith("note-1", "当前记录", "不能丢失的输入");
});

it("flush drains edits typed while a preceding local save is pending", async () => {
  let finish!: (value: typeof note) => void;
  vi.mocked(listNotes).mockResolvedValue([note]);
  vi.mocked(updateNote).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const ref = createRef<NotesPageHandle>();
  render(<NotesPage ref={ref} />);
  const body = await screen.findByRole("textbox", { name: "笔记正文" });
  fireEvent.change(body, { target: { value: "第一版" } });
  let flushing!: Promise<void>;
  act(() => { flushing = ref.current!.flush(); });
  fireEvent.change(body, { target: { value: "第二版" } });
  await act(async () => { finish({ ...note, content: "第一版" }); await flushing; });
  expect(updateNote).toHaveBeenCalledTimes(2);
  expect(updateNote).toHaveBeenLastCalledWith("note-1", "", "第二版");
  expect(body).toHaveValue("第二版");
});

it("prevents native close until edits are saved and retains the window on failure", async () => {
  vi.mocked(isTauri).mockReturnValue(true);
  vi.mocked(listNotes).mockResolvedValue([note]);
  vi.mocked(updateNote).mockRejectedValueOnce(new Error("不能落盘"));
  const blocked = vi.fn();
  render(<NotesPage onSaveBlocked={blocked} />);
  fireEvent.change(await screen.findByRole("textbox", { name: "笔记正文" }), { target: { value: "关闭前的内容" } });
  const preventDefault = vi.fn();
  await act(async () => { await native.close!({ preventDefault }); });
  expect(preventDefault).toHaveBeenCalledOnce();
  expect(native.destroy).not.toHaveBeenCalled();
  expect(blocked).toHaveBeenCalledOnce();
  expect(screen.getByRole("textbox", { name: "笔记正文" })).toHaveValue("关闭前的内容");
  expect(screen.getByRole("textbox", { name: "笔记正文" })).toBeEnabled();
  await act(async () => { await native.close!({ preventDefault }); });
  expect(updateNote).toHaveBeenLastCalledWith("note-1", "", "关闭前的内容");
  expect(native.destroy).toHaveBeenCalledOnce();
});

it("waits for native save completion and removes its close listener on unmount", async () => {
  vi.mocked(isTauri).mockReturnValue(true);
  vi.mocked(listNotes).mockResolvedValue([note]);
  let finish!: (value: typeof note) => void;
  vi.mocked(updateNote).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const view = render(<NotesPage />);
  fireEvent.change(await screen.findByRole("textbox", { name: "笔记正文" }), { target: { value: "立即关闭" } });
  let closing!: Promise<void>;
  act(() => { closing = native.close!({ preventDefault: vi.fn() }); });
  expect(native.destroy).not.toHaveBeenCalled();
  expect(screen.getByRole("textbox", { name: "笔记正文" })).toBeDisabled();
  await act(async () => { finish({ ...note, content: "立即关闭" }); await closing; });
  expect(native.destroy).toHaveBeenCalledOnce();
  view.unmount();
  expect(native.unlisten).toHaveBeenCalledOnce();
});

it("keeps the current version's saved/search state when an older index request finishes late", async () => {
  const first = { ...note, title: "会议", content: "旧正文", displayTitle: "会议", retrieval: { ...note.retrieval, keyword: "ready" as const, semantic: "pending" as const } };
  vi.mocked(listNotes).mockResolvedValue([first]);
  let finish!: (value: typeof first) => void;
  vi.mocked(indexNote).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  const ref = createRef<NotesPageHandle>();
  render(<NotesPage ref={ref} />);
  fireEvent.click(await screen.findByRole("button", { name: "重试智能检索" }));
  expect(await screen.findByText(/正在准备智能检索/)).toBeVisible();
  fireEvent.change(screen.getByRole("textbox", { name: "笔记正文" }), { target: { value: "新正文" } });
  await act(async () => { await ref.current!.flush(); });
  await act(async () => { finish(first); });
  expect(screen.getByRole("textbox", { name: "笔记正文" })).toHaveValue("新正文");
  expect(screen.getByText("已保存，可按关键词查找")).toBeVisible();
  expect(screen.queryByText(/正在准备智能检索/)).not.toBeInTheDocument();
});

it("searches title and body through the local notes API", async () => {
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  render(<NotesPage />);
  fireEvent.change(screen.getByRole("textbox", { name: "搜索笔记" }), {
    target: { value: "TiDB" },
  });
  await act(async () => { await vi.advanceTimersByTimeAsync(219); });
  expect(listNotes).not.toHaveBeenCalledWith("TiDB");
  await act(async () => { await vi.advanceTimersByTimeAsync(1); });
  expect(listNotes).toHaveBeenCalledExactlyOnceWith("TiDB");
  expect(screen.getByText("没有匹配的笔记")).toBeVisible();
});

it("deletes a selected note after confirmation", async () => {
  vi.mocked(listNotes).mockResolvedValue([{
    ...note,
    title: "会议记录",
    content: "正文",
    displayTitle: "会议记录",
  }]);
  render(<NotesPage />);
  fireEvent.click(await screen.findByRole("button", { name: /会议记录/ }));
  await screen.findByRole("textbox", { name: "笔记正文" });
  fireEvent.click(screen.getByRole("button", { name: "删除笔记" }));
  expect(screen.getByRole("dialog")).toHaveTextContent("不会再出现在搜索和 AI 回答中");
  fireEvent.click(screen.getByRole("button", { name: "删除" }));
  await waitFor(() => expect(deleteNote).toHaveBeenCalledWith("note-1"));
  expect(await screen.findByText("记下此刻值得留下的东西")).toBeVisible();
});
