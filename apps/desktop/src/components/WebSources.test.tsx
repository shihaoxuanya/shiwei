import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AddLinkDialog, WebSnapshotContent } from "./WebSources";
import { ImportActions, ImportFeedback, LibraryPage } from "./LibraryPages";
import { SourcePanel, SourceMatchCard } from "./chat/Sources";
import { useImportStore } from "../stores/import-store";
import { getWebSnapshot, openWebSource, openSource, type SourceSummary, type WebSnapshot } from "../lib/import";
import { searchLocal } from "../lib/search";
import { useLibraryLocationStore } from "../lib/library-location";

vi.mock("../lib/import", async original => ({ ...await original<typeof import("../lib/import")>(), getWebSnapshot: vi.fn(), openWebSource: vi.fn(async () => {}), openSource: vi.fn(async () => {}) }));
vi.mock("../lib/search", () => ({ searchLocal: vi.fn(async () => []) }));
const snapshot: WebSnapshot = { sourceId: "w1", title: "迁移文章", originalUrl: "https://example.com/a", finalUrl: "https://example.com/article", capturedAt: "2026-09-10T01:00:00Z", sections: [{ heading: "背景", headingPath: ["背景"], blocks: [{ kind: "paragraph", text: "第一段" }] }, { headingPath: ["迁移"], blocks: [{ kind: "paragraph", text: "<img src='https://remote/image' onerror='alert(1)'>" }, { kind: "code", text: "<script>alert('source')</script>" }] }], chunks: [{ chunkId: "c2", sectionIndex: 1 }] };
const source: SourceSummary = { id: "w1", filename: "迁移文章", sourceType: "web_page", originalPath: "", storedPath: "C:/archive.html", contentHash: "hash", size: 10, importedAt: snapshot.capturedAt, status: "searchable", originalUrl: snapshot.originalUrl, finalUrl: snapshot.finalUrl, capturedAt: snapshot.capturedAt };
beforeEach(() => { vi.clearAllMocks(); useImportStore.setState({ status: "idle", report: null, progress: null, error: null, lastInput: undefined }); useLibraryLocationStore.setState({ locked: false, activities: {} }); vi.mocked(getWebSnapshot).mockResolvedValue(snapshot); });

it("validates links, supports keyboard cancel and starts saving via the existing import store", async () => {
  const addUrl = vi.fn(async () => {}), close = vi.fn(); useImportStore.setState({ addUrl });
  render(<AddLinkDialog onClose={close}/>); const input = screen.getByRole("textbox", { name: "网页地址" }); expect(input).toHaveFocus();
  fireEvent.change(input, { target: { value: "javascript:alert(1)" } }); fireEvent.click(screen.getByRole("button", { name: "保存网页" })); expect(addUrl).not.toHaveBeenCalled(); expect(screen.getByRole("alert")).toBeVisible();
  fireEvent.change(input, { target: { value: "https://user:pass@example.com" } }); fireEvent.click(screen.getByRole("button", { name: "保存网页" })); expect(addUrl).not.toHaveBeenCalled();
  fireEvent.change(input, { target: { value: "  https://example.com/文章  " } }); fireEvent.submit(input.closest("form")!); expect(addUrl).toHaveBeenCalledWith("https://example.com/文章"); expect(close).toHaveBeenCalled();
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" }); expect(close).toHaveBeenCalledTimes(2);
});
it("disables link actions while importing or migrating", () => {
  const view = render(<ImportActions/>); expect(screen.getByRole("button", { name: "添加链接" })).toBeEnabled();
  act(() => useImportStore.setState({ status: "importing" })); expect(screen.getByRole("button", { name: "添加链接" })).toBeDisabled();
  act(() => { useImportStore.setState({ status: "idle" }); useLibraryLocationStore.setState({ locked: true }); }); expect(screen.getByRole("button", { name: "添加链接" })).toBeDisabled(); view.unmount();
});
it("retries a failed link as import_url rather than a file path", () => {
  const addUrl = vi.fn(async () => {}), addPaths = vi.fn(async () => {});
  useImportStore.setState({ addUrl, addPaths, status: "success", report: { jobId: "j", imported: [], skipped: [], failed: [{ path: snapshot.originalUrl, inputKind: "url", reason: "页面不可访问" }], summary: { imported: 0, skipped: 0, failed: 1 } } });
  render(<ImportFeedback/>); fireEvent.click(screen.getByRole("button", { name: "重试" })); expect(addUrl).toHaveBeenCalledWith(snapshot.originalUrl); expect(addPaths).not.toHaveBeenCalled();
});
it("renders saved snapshot as inert text, highlights the exact cited paragraph, and fetches no remote assets", async () => {
  const view = render(<WebSnapshotContent sourceId="w1" focusChunkId="c2"/>);
  await screen.findByText("第一段"); expect(getWebSnapshot).toHaveBeenCalledWith("w1");
  expect(screen.getByRole("region", { name: "引用对应段落" })).toHaveTextContent("<img");
  expect(view.container.querySelectorAll("img,iframe,script,video,a")).toHaveLength(0); expect(view.container.querySelector("pre code")).toHaveTextContent("<script>");
  expect(screen.getByText(/保存于/)).toBeVisible();
});
it("loads citations from authoritative source id without fake page labels or HTML file opening", async () => {
  render(<SourcePanel onClose={vi.fn()} citation={{ citationId: "S1", documentId: "d", chunkId: "c2", sourceId: "w1", sourceType: "web_page", sourceFilename: source.filename, storedPath: source.storedPath, snippet: "第一段" }}/>);
  await screen.findByText("第一段"); expect(screen.queryByText(/第 .* 页/)).not.toBeInTheDocument(); expect(screen.queryByRole("button", { name: "打开文件" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "打开原网页" })); await waitFor(() => expect(openWebSource).toHaveBeenCalledWith("w1")); expect(openSource).not.toHaveBeenCalled();
});
it("opens library and file-match webpages as offline snapshots by default", async () => {
  const view = render(<LibraryPage sources={[source]} loading={false} error="" onRefresh={vi.fn()}/>);
  expect(screen.getByText(/网页 · example.com/)).toBeVisible(); fireEvent.click(screen.getByRole("button", { name: "查看已保存内容：迁移文章" })); await screen.findByText("第一段"); expect(openSource).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "关闭网页快照" })); view.unmount();
  render(<SourceMatchCard source={{ ...source, sourceId: source.id }} onChoose={vi.fn()}/>); fireEvent.click(screen.getByRole("button", { name: "查看已保存内容" })); await screen.findByText("第一段"); expect(openSource).not.toHaveBeenCalled();
});
it("includes webpage body matches in library search", async () => {
  vi.mocked(searchLocal).mockImplementation(async (_q, _limit, type) => type === "web_page" ? [{ chunkId: "c", documentId: "d", sourceId: "w1", sourceType: "web_page", content: "Oracle 迁移38～50天", documentTitle: source.filename, filename: source.filename, lexicalScore: 1, matchedBy: [] }] : []);
  render(<LibraryPage sources={[source]} loading={false} error="" onRefresh={vi.fn()}/>); fireEvent.change(screen.getByRole("textbox", { name: "搜索资料名称或正文" }), { target: { value: "Oracle" } });
  await screen.findByText("Oracle", { selector: "mark" }); expect(searchLocal).toHaveBeenCalledWith("Oracle", 100, "web_page");
});
