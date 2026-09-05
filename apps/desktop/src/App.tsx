import {
  Archive,
  Bot,
  CheckCircle2,
  FilePlus2,
  FolderPlus,
  Home,
  MessageSquareText,
  RefreshCw,
  Search,
  Settings,
  ExternalLink,
  Trash2,
  RotateCcw,
  FolderSearch,
  Sparkles,
  NotebookPen,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { Button } from "./components/ui/button";
import { ChatPage } from "./components/chat/ChatPage";
export { ChatPage } from "./components/chat/ChatPage";
export { CitationChip, SourceMatchCard } from "./components/chat/Sources";
import { cn } from "./lib/cn";
import {
  deleteSource,
  listSources,
  openSource,
  reindexSource,
  revealSource,
  type SourceSummary,
} from "./lib/import";
import { SettingsPage } from "./components/SettingsPage";
import { ReleaseInfrastructure } from "./components/ReleaseInfrastructure";
import { useImportStore } from "./stores/import-store";
import { useWorkerStore } from "./stores/worker-store";
import { NotesPage, type NotesPageHandle } from "./components/notes/NotesPage";

const navigation = [
  { id: "home", label: "首页", icon: Home },
  { id: "chat", label: "对话", icon: MessageSquareText },
  { id: "library", label: "资料", icon: Archive },
  { id: "notes", label: "笔记", icon: NotebookPen },
  { id: "settings", label: "设置", icon: Settings },
] as const;

type PageId = (typeof navigation)[number]["id"];

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function LibraryPage() {
  const [sources, setSources] = useState<SourceSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [workingId, setWorkingId] = useState<string | null>(null);

  useEffect(() => {
    void listSources()
      .then(setSources)
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : String(reason)),
      );
  }, []);

  const normalizedQuery = query.trim().toLocaleLowerCase();
  const filtered = sources.filter(
    (source) =>
      !normalizedQuery ||
      `${source.filename} ${source.originalPath}`
        .toLocaleLowerCase()
        .includes(normalizedQuery),
  );

  const handleReindex = async (source: SourceSummary) => {
    setWorkingId(source.id);
    setError(null);
    try {
      await reindexSource(source.id);
      setSources(await listSources());
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorkingId(null);
    }
  };

  const handleDelete = async (source: SourceSummary) => {
    if (
      !window.confirm(
        `确定删除“${source.filename}”吗？拾微保存的副本和索引也会被删除。`,
      )
    )
      return;
    setWorkingId(source.id);
    setError(null);
    try {
      await deleteSource(source.id);
      setSources((current) => current.filter((item) => item.id !== source.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setWorkingId(null);
    }
  };

  return (
    <section className="mx-auto max-w-5xl px-10 py-12">
      <p className="mb-3 text-xs font-semibold tracking-[0.2em] text-indigo">
        本地资料库
      </p>
      <div className="flex items-end justify-between gap-6">
        <div>
          <h1 className="font-serif text-4xl font-semibold text-ink">资料</h1>
          <p className="mt-3 text-sm text-muted">
            原始文件保存在本机，索引可以随时重新建立。
          </p>
        </div>
        <span className="text-sm text-muted">{sources.length} 个来源</span>
      </div>

      {error && (
        <p className="mt-6 rounded-xl bg-red-50 p-4 text-sm text-red-700">
          {error}
        </p>
      )}

      <div className="mt-7 flex items-center gap-3 rounded-xl border border-line bg-panel px-4 py-2.5">
        <Search className="size-4 text-muted" />
        <input
          className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-[#aaa79d]"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="按文件名或原始路径筛选"
        />
      </div>

      <div className="mt-4 overflow-hidden rounded-2xl border border-line bg-panel">
        {sources.length === 0 ? (
          <div className="px-8 py-16 text-center text-sm text-muted">
            尚未导入资料。回到首页添加文件或文件夹。
          </div>
        ) : (
          filtered.map((source) => (
            <div
              key={source.id}
              className="grid grid-cols-[1fr_90px_90px_140px] items-center gap-4 border-b border-line px-6 py-4 last:border-b-0"
            >
              <div className="min-w-0">
                <p className="truncate text-sm font-medium text-ink">
                  {source.filename}
                </p>
                <p className="mt-1 truncate text-xs text-muted">
                  {source.originalPath}
                </p>
                {source.error && (
                  <p className="mt-2 text-xs leading-5 text-red-700">
                    {source.error}
                  </p>
                )}
                <p className="mt-1 text-[11px] text-[#98958b]">
                  {(source.filename.split(".").pop() ?? "文件").toUpperCase()} ·{" "}
                  {new Date(source.importedAt).toLocaleString("zh-CN")}
                </p>
              </div>
              <span className="text-xs text-muted">
                {formatBytes(source.size)}
              </span>
              <span
                className={cn(
                  "text-xs font-medium",
                  source.status === "failed" ? "text-red-600" : "text-indigo",
                )}
              >
                {source.status === "processing"
                  ? "正在理解"
                  : source.status === "searchable"
                    ? "可搜索"
                    : "处理失败"}
              </span>
              <div className="flex justify-end gap-1">
                <button
                  className="rounded-lg p-2 text-muted hover:bg-[#f0eee7] hover:text-ink"
                  title="打开文件"
                  onClick={() =>
                    void openSource(source.originalPath).catch(() =>
                      openSource(source.storedPath),
                    )
                  }
                >
                  <ExternalLink className="size-4" />
                </button>
                <button
                  className="rounded-lg p-2 text-muted hover:bg-[#f0eee7] hover:text-ink"
                  title="在资源管理器中显示"
                  onClick={() =>
                    void revealSource(source.originalPath).catch(() =>
                      revealSource(source.storedPath),
                    )
                  }
                >
                  <FolderSearch className="size-4" />
                </button>
                <button
                  className="rounded-lg p-2 text-muted hover:bg-[#f0eee7] hover:text-ink disabled:opacity-40"
                  title="重新理解"
                  disabled={workingId === source.id}
                  onClick={() => void handleReindex(source)}
                >
                  <RotateCcw
                    className={cn(
                      "size-4",
                      workingId === source.id && "animate-spin",
                    )}
                  />
                </button>
                <button
                  className="rounded-lg p-2 text-muted hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
                  title="删除"
                  disabled={workingId === source.id}
                  onClick={() => void handleDelete(source)}
                >
                  <Trash2 className="size-4" />
                </button>
              </div>
            </div>
          ))
        )}
      </div>
    </section>
  );
}

function HomePage({ onNewNote }: { onNewNote: () => void }) {
  const { status, result, error, check } = useWorkerStore();
  const importState = useImportStore();
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    void check();
  }, [check]);

  useEffect(() => {
    if (!isTauri()) return;
    let removeListener: (() => void) | undefined;
    void getCurrentWebview()
      .onDragDropEvent((event) => {
        if (event.payload.type === "enter" || event.payload.type === "over") {
          setDragging(true);
        } else if (event.payload.type === "leave") {
          setDragging(false);
        } else if (event.payload.type === "drop") {
          setDragging(false);
          void importState.addPaths(event.payload.paths);
        }
      })
      .then((unlisten) => {
        removeListener = unlisten;
      });
    return () => removeListener?.();
  }, [importState.addPaths]);

  const isConnected = status === "connected";
  const isPreview = status === "preview";

  return (
    <main className="mx-auto grid min-h-full max-w-6xl grid-cols-[1.25fr_0.75fr] gap-10 px-12 py-12">
      <section>
        <div className="mb-10 flex items-center gap-2 text-sm text-indigo">
          <Sparkles className="size-4" />
          <span>个人 AI 记忆工具</span>
        </div>
        <h1 className="max-w-2xl font-serif text-[48px] font-semibold leading-[1.18] tracking-[-0.025em] text-ink">
          你不用整理，
          <br />
          它替你记住。
        </h1>
        <p className="mt-6 max-w-xl text-base leading-8 text-muted">
          把过去的文件和文件夹交给拾微。需要的时候，用一句话把曾经看过、学过和做过的事情找回来。
        </p>

        <div
          className={cn(
            "mt-10 rounded-2xl border border-dashed bg-[#f8f7f2] px-8 py-12 text-center shadow-[0_12px_40px_rgba(50,48,38,0.04)] transition-colors",
            dragging ? "border-indigo bg-[#f0f0fa]" : "border-[#b9b6d6]",
          )}
        >
          <div className="mx-auto flex size-12 items-center justify-center rounded-xl bg-[#ececf7] text-indigo">
            <FolderPlus className="size-6" />
          </div>
          <h2 className="mt-5 text-lg font-semibold text-ink">
            把文件或文件夹拖到这里
          </h2>
          <p className="mt-2 text-sm text-muted">
            无需分类，拾微会在本机保存、理解并建立索引
          </p>
          <div className="mt-6 flex justify-center gap-3">
            <Button
              onClick={() => void importState.addFiles()}
              disabled={
                importState.status === "importing" ||
                importState.status === "selecting"
              }
            >
              <FilePlus2 className="size-4" /> 添加文件
            </Button>
            <Button
              variant="secondary"
              onClick={() => void importState.addFolder()}
              disabled={
                importState.status === "importing" ||
                importState.status === "selecting"
              }
            >
              <FolderPlus className="size-4" /> 添加文件夹
            </Button>
          </div>
          <p className="mt-4 text-xs leading-5 text-muted">
            支持 PDF（含扫描件）、Word、PPT、Excel、图片与文本。单文件不超过
            100MB，PDF 不超过 300 页。
          </p>
          <div className="mt-6 flex items-center justify-center gap-3 border-t border-line pt-5">
            <span className="text-xs text-muted">自己的信息</span>
            <Button variant="ghost" onClick={onNewNote}>
              <NotebookPen className="size-4" />
              记一下
            </Button>
          </div>
        </div>

        {importState.status !== "idle" && (
          <div
            className={cn(
              "mt-4 rounded-xl border px-4 py-3 text-sm",
              importState.status === "error"
                ? "border-red-200 bg-red-50 text-red-700"
                : "border-line bg-panel text-ink",
            )}
          >
            {importState.status === "selecting" && "正在等待你选择资料……"}
            {importState.status === "importing" && (
              <div>
                <div className="flex items-center justify-between gap-4">
                  <span>
                    {importState.progress?.currentStep ?? "正在读取文件……"}
                  </span>
                  <span className="tabular-nums text-muted">
                    {Math.round((importState.progress?.progress ?? 0) * 100)}%
                  </span>
                </div>
                <div className="mt-2 h-1.5 overflow-hidden rounded-full bg-[#e8e6df]">
                  <div
                    className="h-full rounded-full bg-indigo transition-[width] duration-300"
                    style={{
                      width: `${Math.round((importState.progress?.progress ?? 0) * 100)}%`,
                    }}
                  />
                </div>
                {importState.progress?.filename && (
                  <p className="mt-2 truncate text-xs text-muted">
                    {importState.progress.filename}
                  </p>
                )}
              </div>
            )}
            {importState.status === "success" && importState.report && (
              <div>
                已保存 {importState.report.summary.imported} 个文件
                {importState.report.summary.skipped > 0 &&
                  `，跳过 ${importState.report.summary.skipped} 个重复文件`}
                {importState.report.summary.failed > 0 &&
                  `，${importState.report.summary.failed} 个失败`}
                {importState.report.failed.length > 0 && (
                  <div className="mt-3 space-y-3 border-t border-line pt-3">
                    {importState.report.failed.map((item) => (
                      <div
                        key={item.path}
                        className="flex items-start justify-between gap-3"
                      >
                        <div className="min-w-0">
                          <p className="break-all text-xs font-medium">
                            {item.path.split(/[\\/]/).pop()}
                          </p>
                          <p className="mt-1 text-xs leading-5 text-red-700">
                            {item.reason}
                          </p>
                        </div>
                        <button
                          className="shrink-0 rounded-lg px-2 py-1 text-xs text-indigo hover:bg-indigo/5"
                          onClick={() => void importState.addPaths([item.path])}
                        >
                          重试
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
            {importState.status === "error" && importState.error}
          </div>
        )}

      </section>

      <aside className="self-start rounded-2xl border border-line bg-panel p-6 shadow-[0_16px_50px_rgba(50,48,38,0.05)]">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold tracking-[0.14em] text-muted">
              桌面核心
            </p>
            <h2 className="mt-2 text-xl font-semibold text-ink">
              本地 Worker 状态
            </h2>
          </div>
          <div
            className={cn(
              "flex size-10 items-center justify-center rounded-full",
              isConnected
                ? "bg-emerald-50 text-emerald-600"
                : "bg-[#eeedf5] text-indigo",
            )}
          >
            {isConnected ? (
              <CheckCircle2 className="size-5" />
            ) : (
              <Bot className="size-5" />
            )}
          </div>
        </div>

        <div className="mt-7 space-y-4 text-sm">
          <div className="flex items-center justify-between border-b border-line pb-3">
            <span className="text-muted">连接状态</span>
            <span
              className={cn(
                "font-medium",
                status === "error" ? "text-red-600" : "text-ink",
              )}
            >
              {status === "checking" && "正在连接…"}
              {isConnected && "已连接"}
              {isPreview && "浏览器预览"}
              {status === "error" && "连接失败"}
              {status === "idle" && "等待检查"}
            </span>
          </div>
          <details className="text-xs text-muted"><summary className="cursor-pointer">开发者信息</summary>
          <div className="mt-3 flex items-center justify-between border-b border-line pb-3">
            <span className="text-muted">协议版本</span>
            <span className="font-mono text-xs text-ink">
              {result?.protocolVersion ?? "—"}
            </span>
          </div>
          <div className="flex items-center justify-between border-b border-line pb-3">
            <span className="text-muted">Worker</span>
            <span className="font-mono text-xs text-ink">
              {result?.workerVersion ?? "—"}
            </span>
          </div>
          </details>
        </div>

        {error && (
          <p className="mt-4 rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </p>
        )}

        <Button
          variant="ghost"
          className="mt-5 w-full"
          onClick={() => void check()}
          disabled={status === "checking"}
        >
          <RefreshCw
            className={cn("size-4", status === "checking" && "animate-spin")}
          />
          重新检查
        </Button>

        <p className="mt-5 text-xs leading-5 text-muted">
          原始资料、解析结果和索引默认只保存在这台电脑上。只有回答问题所需的片段会发送给你配置的模型服务。
        </p>
      </aside>
    </main>
  );
}

export default function App() {
  const [page, setPage] = useState<PageId>("home");
  const [chatVisited, setChatVisited] = useState(false);
  const [notesVisited, setNotesVisited] = useState(false);
  const [newNoteRequest, setNewNoteRequest] = useState("");
  const [openNoteRequest, setOpenNoteRequest] = useState({ id: "", key: "" });
  const notesRef = useRef<NotesPageHandle>(null);
  const navigate = (destination: PageId) => {
    if (page === "notes" && destination !== "notes") void notesRef.current?.flush().catch(() => {});
    if (destination === "chat") setChatVisited(true);
    if (destination === "notes") setNotesVisited(true);
    setPage(destination);
  };

  const startNote = () => {
    setNotesVisited(true);
    setNewNoteRequest(crypto.randomUUID());
    setPage("notes");
  };

  const openNote = (id: string) => {
    setNotesVisited(true);
    setOpenNoteRequest({ id, key: crypto.randomUUID() });
    setPage("notes");
  };

  return (
    <div className="grid min-h-screen grid-cols-[188px_1fr] bg-paper text-ink">
      <ReleaseInfrastructure flushNotes={async () => { await notesRef.current?.flush(); }} />
      <aside className="flex h-screen flex-col border-r border-line bg-[#eeece5] px-4 py-5">
        <button
          className="flex items-center gap-3 px-2 py-2 text-left"
          onClick={() => navigate("home")}
        >
          <span className="flex size-9 items-center justify-center rounded-xl bg-indigo font-serif text-lg font-bold text-white">
            拾
          </span>
          <span>
            <strong className="block font-serif text-lg leading-5">拾微</strong>
            <small className="text-[10px] tracking-[0.12em] text-muted">
              SHIWEI
            </small>
          </span>
        </button>

        <nav className="mt-8 space-y-1" aria-label="主导航">
          {navigation.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                className={cn(
                  "flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors",
                  page === item.id
                    ? "bg-white/80 font-medium text-indigo shadow-sm"
                    : "text-muted hover:bg-white/45 hover:text-ink",
                )}
                onClick={() => {
                  if (page !== item.id) navigate(item.id);
                }}
              >
                <Icon className="size-[18px]" />
                {item.label}
              </button>
            );
          })}
        </nav>

        <div className="mt-auto rounded-xl border border-line bg-white/40 p-3 text-xs leading-5 text-muted">
          <span className="mb-1 block font-medium text-ink">本地优先</span>
          资料不会上传到拾微服务器
        </div>
      </aside>

      <div className="h-screen min-w-0 overflow-y-auto">
        {page === "home" && <HomePage onNewNote={startNote} />}
        {chatVisited && (
          <div className="h-full" hidden={page !== "chat"}>
            <ChatPage
              active={page === "chat"}
              onOpenSettings={() => navigate("settings")}
              onOpenNote={openNote}
            />
          </div>
        )}
        {page === "library" && <LibraryPage />}
        {notesVisited && (
          <div className="h-full" hidden={page !== "notes"}>
            <NotesPage
              ref={notesRef}
              active={page === "notes"}
              newRequestKey={newNoteRequest}
              openNoteId={openNoteRequest.id}
              openRequestKey={openNoteRequest.key}
            />
          </div>
        )}
        {page === "settings" && <SettingsPage />}
      </div>
    </div>
  );
}
