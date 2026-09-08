import { useImportStore } from "./import-store";
import { importPaths, chooseFiles, chooseFolder } from "../lib/import";
import { libraryBusyReason, useLibraryLocationStore } from "../lib/library-location";
vi.mock("../lib/import", () => ({ importPaths: vi.fn(), chooseFiles: vi.fn(), chooseFolder: vi.fn() }));
beforeEach(() => {
  vi.clearAllMocks();
  useLibraryLocationStore.setState({ locked: false, progress: null, revision: 0, activities: {} });
  useImportStore.setState({ status: "idle", report: null, progress: null, error: null });
});
it("rejects native dropped files and picker actions while migration is locked", async () => {
  useLibraryLocationStore.setState({ locked: true });
  await useImportStore.getState().addPaths(["D:/synthetic.txt"]);
  await useImportStore.getState().addFiles(); await useImportStore.getState().addFolder();
  expect(importPaths).not.toHaveBeenCalled(); expect(chooseFiles).not.toHaveBeenCalled(); expect(chooseFolder).not.toHaveBeenCalled();
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
