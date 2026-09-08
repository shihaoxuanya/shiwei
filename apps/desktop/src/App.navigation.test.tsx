import { act, fireEvent, render, screen } from "@testing-library/react";
import { forwardRef, useImperativeHandle } from "react";
import App from "./App";
import { useLibraryLocationStore } from "./lib/library-location";
import { listSources } from "./lib/import";

const native = vi.hoisted(() => ({ flush: vi.fn(async () => {}) }));
vi.mock("./components/notes/NotesPage", () => ({
  NotesPage: forwardRef(function TestNotes(_props, ref) {
    useImperativeHandle(ref, () => ({ flush: native.flush }));
    return <div>可继续编辑的笔记</div>;
  }),
}));
vi.mock("./components/chat/ChatPage", () => ({ ChatPage: () => <div>对话内容</div> }));
vi.mock("./components/SettingsPage", () => ({ SettingsPage: () => <div>设置内容</div> }));
vi.mock("./components/ReleaseInfrastructure", () => ({ ReleaseInfrastructure: () => null }));
vi.mock("./components/LibraryPages", () => ({ HomePage: () => <div>首页内容</div>, LibraryPage: () => <div>资料内容</div> }));
vi.mock("./lib/notes", () => ({ listNotes: vi.fn(async () => []) }));
vi.mock("./lib/import", () => ({ listSources: vi.fn(async () => []) }));

beforeEach(() => { vi.clearAllMocks(); native.flush.mockResolvedValue(); useLibraryLocationStore.setState({ locked: false, progress: null, revision: 0, activities: {} }); });

it("keeps the latest destination when several clicks await the same note save", async () => {
  let finish!: () => void;
  const saving = new Promise<void>((resolve) => { finish = resolve; });
  native.flush.mockReturnValue(saving);
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "笔记" }));
  fireEvent.click(screen.getByRole("button", { name: "资料" }));
  fireEvent.click(screen.getByRole("button", { name: "对话" }));
  expect(screen.getByRole("button", { name: "笔记" })).toHaveAttribute("aria-current", "page");
  await act(async () => { finish(); await saving; });
  expect(screen.getByRole("button", { name: "对话" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByText("对话内容")).toBeVisible();
});

it("clicking the current notes destination cancels an earlier pending navigation", async () => {
  let finish!: () => void;
  const saving = new Promise<void>((resolve) => { finish = resolve; });
  native.flush.mockReturnValue(saving);
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "笔记" }));
  fireEvent.click(screen.getByRole("button", { name: "资料" }));
  fireEvent.click(screen.getByRole("button", { name: "笔记" }));
  await act(async () => { finish(); await saving; });
  expect(screen.getByRole("button", { name: "笔记" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByText("可继续编辑的笔记")).toBeVisible();
  expect(screen.queryByText("资料内容")).not.toBeInTheDocument();
});

it("a rejected note flush does not leave the note page or discard its mounted editor", async () => {
  native.flush.mockRejectedValue(new Error("synthetic save failure"));
  render(<App />);
  fireEvent.click(screen.getByRole("button", { name: "笔记" }));
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "设置" })); });
  expect(screen.getByRole("button", { name: "笔记" })).toHaveAttribute("aria-current", "page");
  expect(screen.getByText("可继续编辑的笔记")).toBeVisible();
  expect(screen.queryByText("设置内容")).not.toBeInTheDocument();
});

it("makes the app inert and rejects navigation while moving, then refreshes the actual library", async () => {
  const { container } = render(<App />);
  await act(async () => {});
  fireEvent.click(screen.getByRole("button", { name: "设置" }));
  await act(async () => {});
  const reads = vi.mocked(listSources).mock.calls.length;
  act(() => useLibraryLocationStore.setState({ locked: true }));
  expect(container.querySelector(".app-shell")).toHaveAttribute("inert");
  fireEvent.click(screen.getByRole("button", { name: "对话" }));
  expect(screen.getByRole("button", { name: "设置" })).toHaveAttribute("aria-current", "page");
  await act(async () => { useLibraryLocationStore.setState({ locked: false, revision: 1 }); });
  expect(container.querySelector(".app-shell")).not.toHaveAttribute("inert");
  expect(vi.mocked(listSources).mock.calls.length).toBeGreaterThan(reads);
  await act(async () => { fireEvent.click(screen.getByRole("button", { name: "对话" })); });
  expect(screen.getByRole("button", { name: "对话" })).toHaveAttribute("aria-current", "page");
});
