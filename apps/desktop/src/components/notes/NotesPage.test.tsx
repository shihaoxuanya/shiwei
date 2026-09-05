import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { NotesPage } from "./NotesPage";
import { createNote, deleteNote, listNotes, updateNote } from "../../lib/notes";

vi.mock("../../lib/notes", () => ({
  listNotes: vi.fn(),
  getNote: vi.fn(),
  createNote: vi.fn(),
  updateNote: vi.fn(),
  deleteNote: vi.fn(),
}));

const note = {
  id: "note-1",
  sourceId: "source-1",
  title: "",
  content: "",
  displayTitle: "无标题笔记",
  createdAt: "2026-09-05T01:00:00Z",
  updatedAt: "2026-09-05T01:00:00Z",
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(listNotes).mockResolvedValue([]);
  vi.mocked(createNote).mockResolvedValue(note);
  vi.mocked(updateNote).mockImplementation(async (id, title, content) => ({
    ...note,
    id,
    title,
    content,
    displayTitle: title || content.split("\n")[0] || "无标题笔记",
    updatedAt: "2026-09-05T01:01:00Z",
  }));
  vi.mocked(deleteNote).mockResolvedValue();
});

it("creates a blank note and auto-saves the combined title and body", async () => {
  render(<NotesPage newRequestKey="new-1" />);
  const title = await screen.findByRole("textbox", { name: "笔记标题" });
  fireEvent.change(title, { target: { value: "TiDB会议记录" } });
  fireEvent.change(screen.getByRole("textbox", { name: "笔记正文" }), {
    target: { value: "黄总最终确认需要四套环境。" },
  });
  await waitFor(
    () =>
      expect(updateNote).toHaveBeenCalledWith(
        "note-1",
        "TiDB会议记录",
        "黄总最终确认需要四套环境。",
      ),
    { timeout: 2200 },
  );
  expect(await screen.findByText("已保存")).toBeVisible();
});

it("searches title and body through the local notes API", async () => {
  render(<NotesPage />);
  fireEvent.change(screen.getByRole("textbox", { name: "搜索笔记" }), {
    target: { value: "TiDB" },
  });
  await waitFor(() => expect(listNotes).toHaveBeenCalledWith("TiDB"));
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
