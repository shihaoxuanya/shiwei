import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { open } from "@tauri-apps/plugin-dialog";
import { beginLibraryActivity } from "./library-location";

export type ImportItem = {
  status: "imported" | "duplicate";
  path: string;
  sourceId: string;
  filename: string;
  contentHash?: string;
};

export type ImportReport = {
  jobId: string;
  imported: ImportItem[];
  skipped: ImportItem[];
  failed: Array<{ path: string; reason: string }>;
  summary: { imported: number; skipped: number; failed: number };
  embeddingIndex?: { status?: string; message?: string; requiresRebuild?: boolean };
};

export type ImportProgress = {
  jobId: string;
  progress: number;
  currentStep: string;
  processed: number;
  total: number;
  filename?: string;
};

export type SourceSummary = {
  id: string;
  originalPath: string;
  filename: string;
  storedPath: string;
  contentHash: string;
  size: number;
  mimeType?: string;
  importedAt: string;
  status: "processing" | "searchable" | "failed";
  error?: string;
  retrieval?: { keyword: "ready" | "unavailable"; semantic: "disabled" | "pending" | "ready" | "failed" | "requires_rebuild" };
};

const filters = [
  {
    name: "拾微支持的资料",
    extensions: ["pdf", "docx", "pptx", "xlsx", "md", "txt", "html", "htm", "csv", "png", "jpg", "jpeg", "webp"],
  },
];

export function ensureDesktop(): void {
  if (!isTauri()) {
    throw new Error("文件导入需要在拾微桌面应用中使用");
  }
}

export async function chooseFiles(): Promise<string[]> {
  ensureDesktop();
  const selection = await open({ multiple: true, directory: false, filters });
  if (!selection) return [];
  return Array.isArray(selection) ? selection : [selection];
}

export async function chooseFolder(): Promise<string[]> {
  ensureDesktop();
  const selection = await open({ multiple: false, directory: true });
  return selection ? [selection] : [];
}

export async function importPaths(
  paths: string[],
  onProgress?: (progress: ImportProgress) => void,
): Promise<ImportReport> {
  ensureDesktop();
  if (paths.length === 0) {
    throw new Error("没有选择任何文件");
  }
  const unlisten = onProgress
    ? await listen<ImportProgress>("import-progress", (event) => onProgress(event.payload))
    : undefined;
  try {
    return await invoke<ImportReport>("import_paths", { paths });
  } finally {
    unlisten?.();
  }
}

export async function listSources(): Promise<SourceSummary[]> {
  if (!isTauri()) return [];
  return invoke<SourceSummary[]>("list_sources");
}

export async function deleteSource(sourceId: string): Promise<void> {
  await invoke("delete_source", { sourceId });
}

export async function reindexSource(sourceId: string): Promise<void> {
  const done = beginLibraryActivity("正在重新处理资料");
  try { await invoke("reindex_source", { sourceId }); } finally { done(); }
}

export async function openSource(path: string): Promise<void> {
  await invoke("open_original", { path });
}

export async function revealSource(path: string): Promise<void> {
  await invoke("reveal_original", { path });
}
