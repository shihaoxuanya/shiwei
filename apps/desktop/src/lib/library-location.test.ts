import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import { beginLibraryActivity, chooseLibraryParent, isLibraryRelocating, libraryBusyReason, relocateLibrary, useLibraryLocationStore } from "./library-location";

const native = vi.hoisted(() => ({ desktop: true, close: vi.fn(), unlisten: vi.fn(), unlistenClose: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ isTauri: () => native.desktop, invoke: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn() }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));
vi.mock("@tauri-apps/api/window", () => ({ getCurrentWindow: () => ({ onCloseRequested: native.close }) }));
const result = { dataDir: "E:/记忆/拾微资料库-test", previousDataDir: "D:/old", copiedFiles: 8, retainedOriginal: true as const };

beforeEach(() => {
  vi.clearAllMocks(); native.desktop = true;
  useLibraryLocationStore.setState({ locked: false, progress: null, revision: 0, activities: {} });
  native.close.mockResolvedValue(native.unlistenClose);
  vi.mocked(listen).mockResolvedValue(native.unlisten);
  vi.mocked(invoke).mockResolvedValue(result);
  vi.mocked(open).mockResolvedValue(null);
});

it("uses the directory picker and a cancellation performs no mutation", async () => {
  expect(await chooseLibraryParent()).toBeNull();
  expect(open).toHaveBeenCalledWith({ directory: true, multiple: false, title: "选择新的资料库位置" });
  expect(invoke).not.toHaveBeenCalled();
  expect(isLibraryRelocating()).toBe(false);
});

it("blocks browser preview without claiming a migration", async () => {
  native.desktop = false;
  await expect(chooseLibraryParent()).rejects.toThrow("桌面应用");
  await expect(relocateLibrary("E:/记忆", vi.fn())).rejects.toThrow("桌面应用");
  expect(open).not.toHaveBeenCalled(); expect(invoke).not.toHaveBeenCalled();
});

it("locks synchronously, saves before copying, prevents duplicate calls and listens only until completion", async () => {
  let finishSave!: () => void;
  const saving = new Promise<void>((resolve) => { finishSave = resolve; });
  const flush = vi.fn(() => saving);
  const operation = relocateLibrary("E:/记忆", flush);
  expect(isLibraryRelocating()).toBe(true);
  await expect(relocateLibrary("E:/else", vi.fn())).rejects.toThrow("请勿重复");
  await vi.waitFor(() => expect(flush).toHaveBeenCalledOnce());
  expect(invoke).not.toHaveBeenCalled();
  const preventDefault = vi.fn(); native.close.mock.calls[0][0]({ preventDefault });
  expect(preventDefault).toHaveBeenCalledOnce();
  finishSave();
  await expect(operation).resolves.toEqual(result);
  expect(invoke).toHaveBeenCalledExactlyOnceWith("library_relocate", { destinationParent: "E:/记忆" });
  expect(native.unlisten).toHaveBeenCalledOnce(); expect(native.unlistenClose).toHaveBeenCalledOnce();
  expect(isLibraryRelocating()).toBe(false); expect(useLibraryLocationStore.getState().revision).toBe(1);
});

it("does not start copying when notes cannot save and releases every listener", async () => {
  await expect(relocateLibrary("E:/记忆", async () => { throw new Error("disk full"); })).rejects.toThrow("笔记尚未保存");
  expect(invoke).not.toHaveBeenCalled();
  expect(useLibraryLocationStore.getState()).toMatchObject({ locked: false, revision: 0, progress: null });
  expect(native.unlisten).toHaveBeenCalledOnce(); expect(native.unlistenClose).toHaveBeenCalledOnce();
});

it("releases locks on native failure and requests a reality refresh for uncertain completion", async () => {
  vi.mocked(invoke).mockRejectedValue(new Error("磁盘空间不足"));
  await expect(relocateLibrary("E:/记忆", async () => {})).rejects.toThrow("磁盘空间不足");
  expect(useLibraryLocationStore.getState()).toMatchObject({ locked: false, revision: 1, progress: null });
  expect(native.unlisten).toHaveBeenCalledOnce(); expect(native.unlistenClose).toHaveBeenCalledOnce();
});

it("does not migrate without close/event protection and still cleans up partial registration", async () => {
  vi.mocked(listen).mockRejectedValue(new Error("listener unavailable"));
  await expect(relocateLibrary("E:/记忆", vi.fn())).rejects.toThrow("listener unavailable");
  expect(invoke).not.toHaveBeenCalled(); expect(isLibraryRelocating()).toBe(false);
  expect(native.unlistenClose).toHaveBeenCalledOnce();
});

it("uses only genuine phase/file progress and clears progress on completion", async () => {
  let finish!: (value: typeof result) => void;
  vi.mocked(invoke).mockImplementation(() => new Promise(resolve => { finish = resolve as typeof finish; }));
  const operation = relocateLibrary("E:/记忆", async () => {});
  await vi.waitFor(() => expect(invoke).toHaveBeenCalledOnce());
  const receive = vi.mocked(listen).mock.calls[0][1];
  receive({ payload: { phase: "copying", completedFiles: 2, totalFiles: 8 } } as never);
  expect(useLibraryLocationStore.getState().progress).toEqual({ phase: "copying", completedFiles: 2, totalFiles: 8 });
  receive({ payload: { phase: "verifying", completedFiles: 100, totalFiles: 8 } } as never);
  expect(useLibraryLocationStore.getState().progress).toEqual({ phase: "verifying" });
  receive({ payload: { phase: "made-up", completedFiles: 100, totalFiles: 100 } } as never);
  expect(useLibraryLocationStore.getState().progress?.phase).toBe("verifying");
  finish(result); await operation;
  expect(useLibraryLocationStore.getState().progress).toBeNull();
});

it.each(["正在导入资料", "正在生成回答", "正在重新处理智能检索", "正在更新笔记智能检索"])("rejects while %s and allows retry after the real activity ends", async (label) => {
  const done = beginLibraryActivity(label);
  expect(libraryBusyReason()).toBe(label);
  await expect(relocateLibrary("E:/记忆", vi.fn())).rejects.toThrow(label);
  expect(invoke).not.toHaveBeenCalled(); done();
  await expect(relocateLibrary("E:/记忆", async () => {})).resolves.toEqual(result);
});

it("does not let new work enter while relocation is locked", () => {
  useLibraryLocationStore.setState({ locked: true });
  expect(() => beginLibraryActivity("新的任务")).toThrow("正在迁移");
  expect(useLibraryLocationStore.getState().activities).toEqual({});
});
