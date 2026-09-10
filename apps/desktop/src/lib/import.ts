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
  failed: Array<{ path: string; reason: string; inputKind?: "url" }>;
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
  pageNumber?: number;
  totalPages?: number;
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
  sourceType?: "imported_file" | "web_page";
  originalUrl?: string;
  finalUrl?: string;
  capturedAt?: string;
  bodyHash?: string;
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

export async function importUrl(url: string, onProgress?: (progress: ImportProgress) => void): Promise<ImportReport> {
  ensureDesktop();
  const unlisten = onProgress ? await listen<ImportProgress>("import-progress", event => onProgress(event.payload)) : undefined;
  try { return await invoke<ImportReport>("import_url", { url }); } finally { unlisten?.(); }
}

export type WebSnapshot = {
  sourceId: string; title: string; originalUrl: string; finalUrl: string; capturedAt: string;
  sections: Array<{ heading?: string; headingPath: string[]; blocks: Array<{ kind: string; text: string }> }>;
  chunks?: Array<{ chunkId: string; sectionIndex: number }>;
};

export async function getWebSnapshot(sourceId: string): Promise<WebSnapshot> {
  return invoke<WebSnapshot>("get_web_snapshot", { sourceId });
}
export async function openWebSource(sourceId: string): Promise<void> {
  await invoke("open_web_source", { sourceId });
}

export async function deleteSource(sourceId: string): Promise<void> {
  await invoke("delete_source", { sourceId });
}

export async function reindexSource(sourceId: string, onProgress?: (progress: ImportProgress) => void): Promise<void> {
  const done = beginLibraryActivity("正在重新处理资料");
  let unlisten: (() => void) | undefined;
  try {
    if (onProgress) unlisten = await listen<ImportProgress>("import-progress", event => onProgress(event.payload));
    await invoke("reindex_source", { sourceId });
  } finally { unlisten?.(); done(); }
}

export async function openSource(path: string): Promise<void> {
  await invoke("open_original", { path });
}

export async function revealSource(path: string): Promise<void> {
  await invoke("reveal_original", { path });
}
