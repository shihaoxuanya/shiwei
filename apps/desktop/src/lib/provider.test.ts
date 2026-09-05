import { invoke } from "@tauri-apps/api/core";
import { fetchProviderModels } from "./provider";
import { defaultProvider } from "./provider-presets";
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(), isTauri: () => true }));
it("unwraps the worker models response at the IPC boundary", async () => {
  vi.mocked(invoke).mockResolvedValue({ models: ["test-model"] });
  expect(await fetchProviderModels(defaultProvider, "chat")).toEqual(["test-model"]);
});
