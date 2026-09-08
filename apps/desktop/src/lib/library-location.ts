import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { open } from "@tauri-apps/plugin-dialog";
import { create } from "zustand";

export type LibraryMigrationProgress = { phase: "preparing" | "copying" | "verifying" | "switching"; completedFiles?: number; totalFiles?: number };
export type LibraryRelocation = { dataDir: string; previousDataDir: string; copiedFiles: number; retainedOriginal: true };
type LibraryLocationState = { locked: boolean; progress: LibraryMigrationProgress | null; revision: number; activities: Record<string, string> };
export const useLibraryLocationStore = create<LibraryLocationState>(() => ({ locked: false, progress: null, revision: 0, activities: {} }));
export const isLibraryRelocating = () => useLibraryLocationStore.getState().locked;
export const libraryBusyReason = () => Object.values(useLibraryLocationStore.getState().activities)[0];

/** Synchronous registration closes the gap between starting work and a React render. */
export function beginLibraryActivity(label: string): () => void {
  if (isLibraryRelocating()) throw new Error("资料库正在迁移，请完成后再操作。");
  const id = crypto.randomUUID();
  useLibraryLocationStore.setState((s) => ({ activities: { ...s.activities, [id]: label } }));
  return () => useLibraryLocationStore.setState((s) => { const activities = { ...s.activities }; delete activities[id]; return { activities }; });
}

export async function chooseLibraryParent(): Promise<string | null> {
  if (!isTauri()) throw new Error("更改资料库位置需要在拾微桌面应用中使用。");
  const selected = await open({ directory: true, multiple: false, title: "选择新的资料库位置" });
  return typeof selected === "string" && selected.trim() ? selected : null;
}

export async function relocateLibrary(destinationParent: string, flushNotes: () => Promise<void>): Promise<LibraryRelocation> {
  if (!isTauri()) throw new Error("更改资料库位置需要在拾微桌面应用中使用。");
  if (isLibraryRelocating()) throw new Error("资料库正在迁移，请勿重复操作。");
  const busy = libraryBusyReason();
  if (busy) throw new Error(`${busy}，请完成后再更改位置。`);
  if (!destinationParent.trim()) throw new Error("请先选择新的资料库位置。");
  useLibraryLocationStore.setState({ locked: true, progress: { phase: "preparing" } });
  let unlisten: (() => void) | undefined;
  let unlistenClose: (() => void) | undefined;
  let nativeStarted = false;
  const preventUnload = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
  window.addEventListener("beforeunload", preventUnload);
  try {
    // Register protection before saving or starting the long native operation.
    unlistenClose = await getCurrentWindow().onCloseRequested((event) => { if (isLibraryRelocating()) event.preventDefault(); });
    unlisten = await listen<LibraryMigrationProgress>("library-migration-progress", ({ payload }) => {
      if (!isLibraryRelocating() || !["preparing", "copying", "verifying", "switching"].includes(payload?.phase)) return;
      const validCounts = Number.isInteger(payload.completedFiles) && Number.isInteger(payload.totalFiles) && payload.completedFiles! >= 0 && payload.totalFiles! >= payload.completedFiles!;
      useLibraryLocationStore.setState({ progress: { phase: payload.phase, ...(validCounts ? { completedFiles: payload.completedFiles, totalFiles: payload.totalFiles } : {}) } });
    });
    try { await flushNotes(); } catch { throw new Error("笔记尚未保存，未开始迁移。请先重试保存笔记，再更改位置。"); }
    nativeStarted = true;
    const result = await invoke<LibraryRelocation>("library_relocate", { destinationParent });
    return result;
  } finally {
    unlisten?.(); unlistenClose?.();
    window.removeEventListener("beforeunload", preventUnload);
    // Also reload after an interrupted reply: the native atomic switch may have committed.
    useLibraryLocationStore.setState((s) => ({ locked: false, progress: null, revision: s.revision + (nativeStarted ? 1 : 0) }));
  }
}
