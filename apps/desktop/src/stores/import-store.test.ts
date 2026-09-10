import { useImportStore } from "./import-store";
import { importPaths, importUrl, chooseFiles, chooseFolder } from "../lib/import";
import { libraryBusyReason, useLibraryLocationStore } from "../lib/library-location";
vi.mock("../lib/import", () => ({ importPaths: vi.fn(), importUrl: vi.fn(), chooseFiles: vi.fn(), chooseFolder: vi.fn() }));
beforeEach(() => {
  vi.clearAllMocks();
  useLibraryLocationStore.setState({ locked: false, progress: null, revision: 0, activities: {} });
  useImportStore.setState({ status: "idle", report: null, progress: null, error: null });
});
it("rejects native dropped files and picker actions while migration is locked", async () => {
  useLibraryLocationStore.setState({ locked: true });
  await useImportStore.getState().addPaths(["D:/synthetic.txt"]);
  await useImportStore.getState().addFiles(); await useImportStore.getState().addFolder();
  await useImportStore.getState().addUrl("https://example.com/article");
  expect(importPaths).not.toHaveBeenCalled(); expect(chooseFiles).not.toHaveBeenCalled(); expect(chooseFolder).not.toHaveBeenCalled();
  expect(importUrl).not.toHaveBeenCalled();
});

it("keeps webpage import activity until its true completion, prevents repeated starts, and retries the URL", async () => {
  let fail!: (error: Error) => void;
  vi.mocked(importUrl).mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject; }));
  const operation = useImportStore.getState().addUrl("https://example.com/article");
  expect(libraryBusyReason()).toBe("正在保存网页");
  await useImportStore.getState().addUrl("https://example.com/duplicate");
  await useImportStore.getState().addPaths(["C:/not-started.txt"]);
  expect(importUrl).toHaveBeenCalledTimes(1); expect(importPaths).not.toHaveBeenCalled();
  fail(new Error("网页暂时无法访问")); await operation;
  expect(libraryBusyReason()).toBeUndefined(); expect(useImportStore.getState().status).toBe("error");
  vi.mocked(importUrl).mockResolvedValue({ jobId: "j", imported: [], skipped: [], failed: [], summary: { imported: 1, skipped: 0, failed: 0 } });
  await useImportStore.getState().retry();
  expect(importUrl).toHaveBeenLastCalledWith("https://example.com/article", expect.any(Function)); expect(useImportStore.getState().status).toBe("success");
});
it("registers actual import work synchronously and releases it on failure", async () => {
  let fail!: (error: Error) => void;
  vi.mocked(importPaths).mockImplementation(() => new Promise((_resolve, reject) => { fail = reject; }));
  const operation = useImportStore.getState().addPaths(["D:/synthetic.txt"]);
  expect(libraryBusyReason()).toBe("正在导入资料");
  fail(new Error("synthetic failure")); await operation;
  expect(libraryBusyReason()).toBeUndefined();
  expect(useImportStore.getState().status).toBe("error");
});
