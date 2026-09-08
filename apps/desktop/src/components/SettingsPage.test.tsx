import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import { SettingsPage } from "./SettingsPage";
import * as api from "../lib/provider";
import { getWorkerInfo } from "../lib/worker";
import { beginLibraryActivity, useLibraryLocationStore } from "../lib/library-location";

const locationNative = vi.hoisted(() => ({ desktop: true, unlisten: vi.fn(), unlistenClose: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ isTauri: () => locationNative.desktop, invoke: vi.fn() }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn() }));
vi.mock("@tauri-apps/api/window", () => ({ getCurrentWindow: () => ({ onCloseRequested: vi.fn(async () => locationNative.unlistenClose) }) }));
vi.mock("../lib/worker", () => ({ getWorkerInfo: vi.fn().mockResolvedValue({ dataDir: "D:/Shiwei-test/data", protocolVersion: "1.0", workerVersion: "0.3.2" }), pingWorker: vi.fn().mockResolvedValue({ status: "pong", protocolVersion: "1.0", workerVersion: "0.3.2" }) }));
vi.mock("../lib/provider-help", () => ({ providerHelpUrl: () => "https://example.invalid/help", openProviderHelp: vi.fn().mockResolvedValue(undefined) }));
vi.mock("../lib/provider", () => ({
  getProviderStatus: vi.fn(), getIndexStatus: vi.fn(), saveProvider: vi.fn(),
  testProvider: vi.fn(), rebuildEmbeddings: vi.fn(), fetchProviderModels: vi.fn(), clearProviderKey: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  locationNative.desktop = true;
  useLibraryLocationStore.setState({ locked: false, progress: null, revision: 0, activities: {} });
  vi.mocked(open).mockResolvedValue(null);
  vi.mocked(listen).mockResolvedValue(locationNative.unlisten);
  vi.mocked(invoke).mockResolvedValue(undefined);
  vi.mocked(getWorkerInfo).mockResolvedValue({ dataDir: "D:/Shiwei-test/data", protocolVersion: "1.0", workerVersion: "0.3.2" });
  vi.mocked(api.getProviderStatus).mockResolvedValue({ configured: false, hasApiKey: false });
  vi.mocked(api.getIndexStatus).mockResolvedValue({ chunkCount: 2 });
  vi.mocked(api.saveProvider).mockResolvedValue();
  vi.mocked(api.rebuildEmbeddings).mockResolvedValue({ indexed: 2 });
  vi.mocked(api.testProvider).mockResolvedValue({ ok: true, chatOk: true, embeddingEnabled: false });
  vi.mocked(api.clearProviderKey).mockResolvedValue();
});

function expandModels() { fireEvent.click(screen.getByText(/模型与服务地址/)); }
function expandRetrieval() { fireEvent.click(screen.getByText("检索方式与模型配置")); }

it("uses selections and resets keys when switching vendors", async () => {
  render(<SettingsPage />);
  await screen.findByText("未配置模型");
  expect(screen.getByText(/密钥保存在系统安全凭据存储中/)).toBeInTheDocument();
  expect(screen.queryByText(/密钥保存在 Windows/)).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("对话 API Key"), { target: { value: "secret" } });
  fireEvent.change(screen.getByLabelText("模型厂商"), { target: { value: "qwen" } });
  expect(screen.getByLabelText("对话 API Key")).toHaveValue("");
  expandModels(); expandRetrieval();
  expect(screen.getByLabelText("对话模型")).toHaveValue("qwen-plus");
  expect(screen.getByLabelText("服务地域")).toHaveValue("https://dashscope.aliyuncs.com/compatible-mode/v1");
  fireEvent.change(screen.getByLabelText("检索方式"), { target: { value: "same" } });
  expect(screen.getByLabelText("向量模型")).toHaveValue("text-embedding-v4");
  fireEvent.change(screen.getByLabelText("模型厂商"), { target: { value: "anthropic" } });
  expect(screen.getByLabelText("检索方式")).toHaveValue("none");
  expect(screen.queryByRole("option", { name: "与对话使用同一服务" })).not.toBeInTheDocument();
});

it("supports custom model IDs and independently configured embeddings", async () => {
  render(<SettingsPage />);
  await screen.findByText("未配置模型");
  expandModels(); expandRetrieval();
  fireEvent.change(screen.getByLabelText("对话模型"), { target: { value: "__custom__" } });
  fireEvent.change(screen.getByLabelText("自定义对话模型 ID"), { target: { value: "ep-private" } });
  fireEvent.change(screen.getByLabelText("检索方式"), { target: { value: "separate" } });
  expect(screen.getByLabelText("语义检索厂商")).toHaveValue("qwen");
  expect(screen.getByLabelText("语义检索 API Key")).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("对话 API Key"), { target: { value: "chat-key" } });
  fireEvent.change(screen.getByLabelText("语义检索 API Key"), { target: { value: "embed-key" } });
  fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
  await waitFor(() => expect(api.saveProvider).toHaveBeenCalledWith(expect.objectContaining({ chatModel: "ep-private", embeddingMode: "separate", apiKey: "chat-key", embeddingApiKey: "embed-key" })));
});

it("discovers model IDs without saving or requiring an embedding key", async () => {
  vi.mocked(api.fetchProviderModels).mockResolvedValue(["new-chat-model"]);
  render(<SettingsPage />);
  await screen.findByText("未配置模型");
  expandModels(); expandRetrieval();
  fireEvent.change(screen.getByLabelText("检索方式"), { target: { value: "separate" } });
  fireEvent.change(screen.getByLabelText("对话 API Key"), { target: { value: "secret" } });
  fireEvent.click(screen.getByRole("button", { name: "刷新对话模型列表" }));
  await screen.findByRole("option", { name: "new-chat-model" });
  expect(api.fetchProviderModels).toHaveBeenCalledWith(expect.objectContaining({ embeddingMode: "none" }), "chat");
  expect(api.saveProvider).not.toHaveBeenCalled();
});

it("saves embedding changes without sending existing content until a separate confirmation", async () => {
  vi.mocked(api.getProviderStatus).mockResolvedValue({ configured: true, hasApiKey: true, baseUrl: "https://api.openai.com/v1", providerId: "openai", chatModel: "gpt-4.1-mini", embeddingModel: "text-embedding-3-small", embeddingMode: "same" });
  render(<SettingsPage />);
  await screen.findByText("配置已保存");
  expect(screen.getByLabelText("对话 API Key")).toHaveValue("");
  expandRetrieval();
  fireEvent.change(screen.getByLabelText("向量模型"), { target: { value: "text-embedding-3-large" } });
  expect(screen.queryByRole("button", { name: "保存并重建语义索引" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
  await screen.findByRole("button", { name: "稍后处理" });
  expect(api.rebuildEmbeddings).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "稍后处理" }));
  expect(api.rebuildEmbeddings).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "开始处理" }));
  await screen.findByText("智能检索已更新，已处理 2 个文本片段；原始资料未改变。");
  expect(api.saveProvider).toHaveBeenCalledWith(expect.objectContaining({ embeddingModel: "text-embedding-3-large", apiKey: "" }));
  expect(vi.mocked(api.saveProvider).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(api.rebuildEmbeddings).mock.invocationCallOrder[0]);
});

it("keeps the form usable when a service has no model-list API", async () => {
  vi.mocked(api.fetchProviderModels).mockRejectedValue(new Error("接口不可用"));
  render(<SettingsPage />);
  await screen.findByText("未配置模型");
  expandModels();
  fireEvent.click(screen.getByRole("button", { name: "刷新对话模型列表" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("仍可选择预设");
  expect(screen.getByLabelText("对话模型")).not.toBeDisabled();
});

it("does not present an obsolete body-only embedding index as active", async () => {
  vi.mocked(api.getIndexStatus).mockResolvedValue({ chunkCount: 2, model: "old-embedding", needsRebuild: true, searchTextVersion: 0 });
  render(<SettingsPage />);
  fireEvent.click(screen.getByRole("tab", { name: "关于与更新" }));
  fireEvent.click(screen.getByText("技术详情"));
  expect(await screen.findByText("需更新，当前使用本地全文检索")).toBeInTheDocument();
  expect(screen.queryByText("old-embedding")).not.toBeInTheDocument();
});

it("tests chat independently and does not need a missing embedding key", async () => {
  render(<SettingsPage />);
  await screen.findByText("未配置模型");
  expandRetrieval();
  fireEvent.change(screen.getByLabelText("检索方式"), { target: { value: "separate" } });
  fireEvent.change(screen.getByLabelText("对话 API Key"), { target: { value: "chat-key" } });
  expect(screen.getByRole("button", { name: "测试智能检索连接" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
  await screen.findByText("对话连接成功");
  expect(api.testProvider).toHaveBeenCalledWith(expect.objectContaining({ embeddingMode: "separate", embeddingApiKey: "" }), "chat");
  expect(screen.getByText("智能检索连接未测试")).toBeInTheDocument();
  expect(api.saveProvider).not.toHaveBeenCalled();
});

it("does not treat a saved configuration as a successful connection or rebuild on chat-model changes", async () => {
  vi.mocked(api.getProviderStatus).mockResolvedValue({ configured: true, hasApiKey: true, baseUrl: "https://api.openai.com/v1", providerId: "openai", chatModel: "gpt-4.1-mini", embeddingModel: "text-embedding-3-small", embeddingMode: "same" });
  render(<SettingsPage />);
  await screen.findByText("配置已保存");
  expect(screen.getByText("对话连接未测试")).toBeInTheDocument();
  expandModels();
  fireEvent.change(screen.getByLabelText("对话模型"), { target: { value: "gpt-4.1" } });
  fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
  await screen.findByText(/连接结果以单独测试为准/);
  expect(api.testProvider).not.toHaveBeenCalled();
  expect(api.rebuildEmbeddings).not.toHaveBeenCalled();
  expect(screen.queryByRole("button", { name: "开始处理" })).not.toBeInTheDocument();
  expect(screen.getByText("对话连接未测试")).toBeInTheDocument();
});

it("keeps failed connection state distinct from saving and permits retry", async () => {
  vi.mocked(api.testProvider).mockRejectedValueOnce(new Error("连接超时"));
  render(<SettingsPage />);
  await screen.findByText("未配置模型");
  fireEvent.change(screen.getByLabelText("对话 API Key"), { target: { value: "test-key" } });
  fireEvent.click(screen.getByRole("button", { name: "测试连接" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("连接超时");
  expect(screen.getByText("对话连接失败")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
  await screen.findByText(/连接结果以单独测试为准/);
  expect(screen.getByText("对话连接失败")).toBeInTheDocument();
});

it("preserves unsaved fields while switching settings sections and supports keyboard tabs", async () => {
  const { rerender } = render(<SettingsPage />);
  await screen.findByText("未配置模型");
  fireEvent.change(screen.getByLabelText("对话 API Key"), { target: { value: "unsaved-key" } });
  fireEvent.keyDown(screen.getByRole("tab", { name: "AI 服务" }), { key: "ArrowRight" });
  expect(screen.getByRole("tab", { name: "数据与隐私" })).toHaveFocus();
  expect(screen.getByRole("tab", { name: "数据与隐私" })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByText("D:/Shiwei-test/data")).toBeVisible();
  expect(screen.getByText(/在线智能检索：建立索引时依次处理/)).toBeVisible();
  expect(screen.queryByRole("button", { name: /备份|导出/ })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("tab", { name: "AI 服务" }));
  expect(screen.getByLabelText("对话 API Key")).toHaveValue("unsaved-key");
  rerender(<SettingsPage initialSection="about" sectionRequestKey="diagnostic-click" />);
  expect(screen.getByRole("tab", { name: "关于与更新" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByText("当前内测版暂不支持在线更新。")).toBeVisible();
});

it("requires explicit confirmation before clearing a saved key", async () => {
  vi.mocked(api.getProviderStatus).mockResolvedValue({ configured: true, hasApiKey: true, baseUrl: "https://api.deepseek.com/v1", providerId: "deepseek", chatModel: "deepseek-v4-flash", embeddingMode: "none" });
  render(<SettingsPage />);
  await screen.findByText("配置已保存");
  fireEvent.click(screen.getByRole("button", { name: "清除已保存的对话密钥" }));
  expect(api.clearProviderKey).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "取消清除" }));
  expect(api.clearProviderKey).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "清除已保存的对话密钥" }));
  fireEvent.click(screen.getByRole("button", { name: "确认清除密钥" }));
  await waitFor(() => expect(api.clearProviderKey).toHaveBeenCalledWith("chat"));
  expect(screen.getByLabelText("对话 API Key")).toHaveValue("");
});

it("tests a separate retrieval service without requiring or testing chat credentials", async () => {
  vi.mocked(api.testProvider).mockResolvedValue({ ok: true, chatOk: false, embeddingEnabled: true, embeddingDimension: 1024 });
  render(<SettingsPage />);
  await screen.findByText("未配置模型");
  expandRetrieval();
  fireEvent.change(screen.getByLabelText("检索方式"), { target: { value: "separate" } });
  fireEvent.change(screen.getByLabelText("语义检索 API Key"), { target: { value: "only-embedding-key" } });
  fireEvent.click(screen.getByRole("button", { name: "测试智能检索连接" }));
  await screen.findByText("智能检索连接成功");
  expect(api.testProvider).toHaveBeenCalledWith(expect.objectContaining({ apiKey: "", embeddingApiKey: "only-embedding-key" }), "embedding");
  expect(screen.getByText("对话连接未测试")).toBeInTheDocument();
  expect(api.saveProvider).not.toHaveBeenCalled();
});

it("cancels choosing a library location without changing paths or starting a migration", async () => {
  render(<SettingsPage initialSection="privacy" />);
  const button = await screen.findByRole("button", { name: "更改位置" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await waitFor(() => expect(open).toHaveBeenCalledOnce());
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByLabelText("当前资料库位置")).toHaveTextContent("D:/Shiwei-test/data");
  expect(invoke).not.toHaveBeenCalledWith("library_relocate", expect.anything());
});

it("requires confirmation, describes the unique child directory and permits Escape before starting", async () => {
  vi.mocked(open).mockResolvedValue("E:/我的记忆");
  render(<SettingsPage initialSection="privacy" />);
  await waitFor(() => expect(screen.getByRole("button", { name: "更改位置" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "更改位置" }));
  const dialog = await screen.findByRole("dialog", { name: "更改资料库位置" });
  expect(dialog).toHaveTextContent("拾微资料库-xxxx");
  expect(dialog).toHaveTextContent("不覆盖现有文件");
  expect(dialog).toHaveTextContent("E:/我的记忆");
  expect(screen.getByRole("button", { name: "取消" })).toHaveFocus();
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(invoke).not.toHaveBeenCalledWith("library_relocate", expect.anything());
});

it("saves notes before migration, locks duplicate confirmation, displays real progress and refreshes the final path", async () => {
  vi.mocked(open).mockResolvedValue("E:/我的记忆");
  let finish!: (value: unknown) => void;
  vi.mocked(invoke).mockImplementation((command) => command === "library_relocate" ? new Promise((resolve) => { finish = resolve; }) : Promise.resolve(undefined));
  const flush = vi.fn(async () => {});
  render(<SettingsPage initialSection="privacy" flushNotes={flush} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "更改位置" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "更改位置" }));
  const confirm = await screen.findByRole("button", { name: "确认迁移" });
  fireEvent.click(confirm); fireEvent.click(confirm);
  await waitFor(() => expect(invoke).toHaveBeenCalledWith("library_relocate", { destinationParent: "E:/我的记忆" }));
  expect(flush).toHaveBeenCalledOnce();
  const migrationCalls = vi.mocked(invoke).mock.calls.filter(([command]) => command === "library_relocate");
  expect(migrationCalls).toHaveLength(1);
  expect(flush.mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(invoke).mock.invocationCallOrder[vi.mocked(invoke).mock.calls.findIndex(([command]) => command === "library_relocate")]);
  const dialog = screen.getByRole("dialog", { name: "正在迁移资料库" });
  fireEvent.keyDown(dialog, { key: "Escape" });
  expect(dialog).toBeInTheDocument(); expect(screen.queryByRole("button", { name: "取消" })).not.toBeInTheDocument();
  const receive = vi.mocked(listen).mock.calls.find(([event]) => event === "library-migration-progress")![1];
  act(() => receive({ payload: { phase: "copying", completedFiles: 3, totalFiles: 10 } } as never));
  expect(dialog).toHaveTextContent("已处理 3 / 10 个文件");
  vi.mocked(getWorkerInfo).mockResolvedValue({ dataDir: "E:/我的记忆/拾微资料库-abc1", protocolVersion: "1.0", workerVersion: "0.3.2" });
  await act(async () => { finish({ dataDir: "E:/我的记忆/拾微资料库-abc1", previousDataDir: "D:/Shiwei-test/data", copiedFiles: 10, retainedOriginal: true }); });
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(screen.getByLabelText("当前资料库位置")).toHaveTextContent("E:/我的记忆/拾微资料库-abc1");
  expect(screen.getByText(/资料库已切换到新位置，已复制 10 个文件/)).toBeVisible();
  expect(locationNative.unlisten).toHaveBeenCalledOnce(); expect(locationNative.unlistenClose).toHaveBeenCalledOnce();
  expect(useLibraryLocationStore.getState().locked).toBe(false);
});

it("does not invoke relocation when pending notes fail to save", async () => {
  vi.mocked(open).mockResolvedValue("E:/我的记忆");
  render(<SettingsPage initialSection="privacy" flushNotes={async () => { throw new Error("save failed"); }} />);
  await waitFor(() => expect(screen.getByRole("button", { name: "更改位置" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "更改位置" }));
  fireEvent.click(await screen.findByRole("button", { name: "确认迁移" }));
  await screen.findByText(/笔记尚未保存，未开始迁移/);
  expect(invoke).not.toHaveBeenCalledWith("library_relocate", expect.anything());
  expect(useLibraryLocationStore.getState().locked).toBe(false);
  expect(screen.getByLabelText("当前资料库位置")).toHaveTextContent("D:/Shiwei-test/data");
});

it("shows an actionable migration failure and rechecks reality rather than asserting a rollback", async () => {
  vi.mocked(open).mockResolvedValue("E:/我的记忆");
  vi.mocked(invoke).mockImplementation(async (command) => { if (command === "library_relocate") throw new Error("目标磁盘不可写"); });
  render(<SettingsPage initialSection="privacy" />);
  await waitFor(() => expect(screen.getByRole("button", { name: "更改位置" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "更改位置" }));
  fireEvent.click(await screen.findByRole("button", { name: "确认迁移" }));
  await screen.findByText(/未能确认迁移完成，原库副本仍保留/);
  expect(screen.getByText(/检查目标磁盘空间及写入权限后重试/)).toBeVisible();
  expect(getWorkerInfo).toHaveBeenCalledTimes(2);
  expect(useLibraryLocationStore.getState().locked).toBe(false);
  expect(screen.getByRole("button", { name: "更改位置" })).toBeEnabled();
});

it("prevents migration while an actual shared background activity is running", async () => {
  const done = beginLibraryActivity("正在生成回答");
  render(<SettingsPage initialSection="privacy" />);
  await screen.findByText("D:/Shiwei-test/data");
  expect(screen.getByRole("button", { name: "更改位置" })).toBeDisabled();
  expect(screen.getByText("正在生成回答，完成后可更改位置。")).toBeVisible();
  act(() => done());
  expect(screen.getByRole("button", { name: "更改位置" })).toBeEnabled();
});

it("locks repeated picker clicks and explains that browser preview cannot move real files", async () => {
  locationNative.desktop = false;
  render(<SettingsPage initialSection="privacy" />);
  await waitFor(() => expect(screen.getByRole("button", { name: "更改位置" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "更改位置" }));
  await screen.findByText(/更改资料库位置需要在拾微桌面应用中使用/);
  expect(open).not.toHaveBeenCalled();
  expect(invoke).not.toHaveBeenCalledWith("library_relocate", expect.anything());
});

it("opens only one native picker even when clicked twice before selection resolves", async () => {
  let cancel!: (value: null) => void;
  vi.mocked(open).mockImplementation(() => new Promise((resolve) => { cancel = resolve; }));
  render(<SettingsPage initialSection="privacy" />);
  await waitFor(() => expect(screen.getByRole("button", { name: "更改位置" })).toBeEnabled());
  const button = screen.getByRole("button", { name: "更改位置" });
  fireEvent.click(button); fireEvent.click(button);
  expect(open).toHaveBeenCalledOnce();
  await act(async () => { cancel(null); });
  expect(screen.getByRole("button", { name: "更改位置" })).toBeEnabled();
});
