import { create } from "zustand";
import { chooseFiles, chooseFolder, importPaths, type ImportProgress, type ImportReport } from "../lib/import";

type ImportStatus = "idle" | "selecting" | "importing" | "success" | "error";

type ImportState = {
  status: ImportStatus;
  report: ImportReport | null;
  progress: ImportProgress | null;
  error: string | null;
  addFiles: () => Promise<void>;
  addFolder: () => Promise<void>;
  addPaths: (paths: string[]) => Promise<void>;
};

const message = (error: unknown) => (error instanceof Error ? error.message : String(error));

export const useImportStore = create<ImportState>((set, get) => ({
  status: "idle",
  report: null,
  progress: null,
  error: null,
  addFiles: async () => {
    if (["importing", "selecting"].includes(get().status)) return;
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
    }
  },
  addFolder: async () => {
    if (["importing", "selecting"].includes(get().status)) return;
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
    }
  },
  addPaths: async (paths) => {
    if (get().status === "importing" || !paths.length) return;
    set({ status: "importing", error: null, report: null, progress: null });
    try {
      const report = await importPaths(paths, (progress) => set({ progress }));
      set({ status: "success", report, progress: null });
    } catch (error) {
      set({ status: "error", error: message(error) });
    }
  },
}));
