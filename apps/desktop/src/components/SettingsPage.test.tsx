import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SettingsPage } from "./SettingsPage";
import * as api from "../lib/provider";

vi.mock("@tauri-apps/api/core", () => ({ isTauri: () => true }));
vi.mock("../lib/provider", () => ({
  getProviderStatus: vi.fn(), getIndexStatus: vi.fn(), saveProvider: vi.fn(),
  testProvider: vi.fn(), rebuildEmbeddings: vi.fn(), fetchProviderModels: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(api.getProviderStatus).mockResolvedValue({ configured: false, hasApiKey: false });
  vi.mocked(api.getIndexStatus).mockResolvedValue({ chunkCount: 2 });
  vi.mocked(api.saveProvider).mockResolvedValue();
  vi.mocked(api.rebuildEmbeddings).mockResolvedValue({ indexed: 2 });
});

it("uses selections and resets keys when switching vendors", async () => {
  render(<SettingsPage />);
  await screen.findByText("未配置模型");
  fireEvent.change(screen.getByLabelText("对话 API Key"), { target: { value: "secret" } });
  fireEvent.change(screen.getByLabelText("模型厂商"), { target: { value: "qwen" } });
  expect(screen.getByLabelText("对话 API Key")).toHaveValue("");
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
  fireEvent.change(screen.getByLabelText("检索方式"), { target: { value: "separate" } });
  fireEvent.change(screen.getByLabelText("对话 API Key"), { target: { value: "secret" } });
  fireEvent.click(screen.getByRole("button", { name: "刷新对话模型列表" }));
  await screen.findByRole("option", { name: "new-chat-model" });
  expect(api.fetchProviderModels).toHaveBeenCalledWith(expect.objectContaining({ embeddingMode: "none" }), "chat");
  expect(api.saveProvider).not.toHaveBeenCalled();
});

it("saves a model-only change before rebuilding and keeps saved credentials hidden", async () => {
  vi.mocked(api.getProviderStatus).mockResolvedValue({ configured: true, hasApiKey: true, baseUrl: "https://api.openai.com/v1", providerId: "openai", chatModel: "gpt-4.1-mini", embeddingModel: "text-embedding-3-small", embeddingMode: "same" });
  render(<SettingsPage />);
  await screen.findByText("配置已保存");
  expect(screen.getByLabelText("对话 API Key")).toHaveValue("");
  fireEvent.change(screen.getByLabelText("向量模型"), { target: { value: "text-embedding-3-large" } });
  fireEvent.click(screen.getByRole("button", { name: "保存并重建语义索引" }));
  await screen.findByText("语义索引已建立，共处理 2 个知识片段");
  expect(api.saveProvider).toHaveBeenCalledWith(expect.objectContaining({ embeddingModel: "text-embedding-3-large", apiKey: "" }));
  expect(vi.mocked(api.saveProvider).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(api.rebuildEmbeddings).mock.invocationCallOrder[0]);
});

it("keeps the form usable when a service has no model-list API", async () => {
  vi.mocked(api.fetchProviderModels).mockRejectedValue(new Error("接口不可用"));
  render(<SettingsPage />);
  await screen.findByText("未配置模型");
  fireEvent.click(screen.getByRole("button", { name: "刷新对话模型列表" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("仍可选择预设");
  expect(screen.getByLabelText("对话模型")).not.toBeDisabled();
});

it("does not present an obsolete body-only embedding index as active", async () => {
  vi.mocked(api.getIndexStatus).mockResolvedValue({ chunkCount: 2, model: "old-embedding", needsRebuild: true, searchTextVersion: 0 });
  render(<SettingsPage />);
  expect(await screen.findByText("需更新，当前使用本地全文检索")).toBeInTheDocument();
  expect(screen.queryByText("old-embedding")).not.toBeInTheDocument();
});
