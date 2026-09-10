import { create } from "zustand";
import { chooseFiles, chooseFolder, importPaths, importUrl, type ImportProgress, type ImportReport } from "../lib/import";
import { beginLibraryActivity, isLibraryRelocating } from "../lib/library-location";

type ImportStatus = "idle" | "selecting" | "importing" | "success" | "error";

type ImportState = {
  status: ImportStatus;
  report: ImportReport | null;
  progress: ImportProgress | null;
  error: string | null;
  addFiles: () => Promise<void>;
  addFolder: () => Promise<void>;
  addPaths: (paths: string[]) => Promise<void>;
  addUrl: (url: string) => Promise<void>;
  retry: () => Promise<void>;
  lastInput?: { kind: "url"; url: string } | { kind: "paths"; paths: string[] };
};

const message = (error: unknown) => (error instanceof Error ? error.message : String(error));

export const useImportStore = create<ImportState>((set, get) => ({
  status: "idle",
  report: null,
  progress: null,
  error: null,
  addFiles: async () => {
    if (isLibraryRelocating() || ["importing", "selecting"].includes(get().status)) return;
    const done = beginLibraryActivity("正在选择或导入资料");
    set({ status: "selecting", error: null });
    try {
      const paths = await chooseFiles();
      if (paths.length === 0) {
        set({ status: "idle" });
        return;
      }
      await get().addPaths(paths);
    } catch (error) {
      set({ status: "error", error: message(error) });
    } finally { done(); }
  },
  addFolder: async () => {
    if (isLibraryRelocating() || ["importing", "selecting"].includes(get().status)) return;
    const done = beginLibraryActivity("正在选择或导入资料");
    set({ status: "selecting", error: null });
    try {
      const paths = await chooseFolder();
      if (paths.length === 0) {
        set({ status: "idle" });
        return;
      }
      await get().addPaths(paths);
    } catch (error) {
      set({ status: "error", error: message(error) });
    } finally { done(); }
  },
  addPaths: async (paths) => {
    if (isLibraryRelocating() || get().status === "importing" || !paths.length) return;
    const done = beginLibraryActivity("正在导入资料");
    set({ status: "importing", error: null, report: null, progress: null, lastInput: { kind: "paths", paths } });
    try {
      const report = await importPaths(paths, (progress) => set({ progress }));
      set({ status: "success", report, progress: null });
    } catch (error) {
      set({ status: "error", error: message(error) });
    } finally { done(); }
  },
  addUrl: async (url) => {
    if (isLibraryRelocating() || ["importing", "selecting"].includes(get().status)) return;
    const done = beginLibraryActivity("正在保存网页");
    set({ status: "importing", error: null, report: null, progress: null, lastInput: { kind: "url", url } });
    try {
      const report = await importUrl(url, progress => set({ progress }));
      set({ status: "success", report, progress: null });
    } catch (error) { set({ status: "error", error: message(error) }); }
    finally { done(); }
  },
  retry: async () => {
    const input = get().lastInput;
    if (input?.kind === "url") await get().addUrl(input.url);
    else if (input) await get().addPaths(input.paths);
  },
}));
