import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { NotebookPen, Plus, Search, Trash2, X } from "lucide-react";
import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";
import { beginLibraryActivity, isLibraryRelocating, useLibraryLocationStore } from "../../lib/library-location";
import { Button } from "../ui/button";
import {
  createNote,
  deleteNote,
  getNote,
  indexNote,
  listNotes,
  updateNote,
  type Note,
} from "../../lib/notes";
import "./notes.css";

export type NotesPageHandle = {
  flush: () => Promise<void>;
};

type SaveState = "idle" | "saving" | "saved" | "error";
const LAST_NOTE = "shiwei.notes.last-open";
const SCROLL_KEY = "shiwei.notes.scroll";

function readPreference(key: string) {
  try { return window.localStorage.getItem(key); } catch { return null; }
}
function writePreference(key: string, value: string) {
  try { window.localStorage.setItem(key, value); } catch { /* Local UI preferences are optional. */ }
}

function retrievalLabel(note: Note | undefined, indexing: boolean) {
  if (!note?.retrieval || note.retrieval.revision !== note.updatedAt) return "已保存 · 搜索状态待确认";
  if (note.retrieval.keyword === "empty") return "草稿已保存 · 输入内容后可检索";
  if (note.retrieval.keyword !== "ready") return "已保存 · 正在更新搜索";
  if (indexing) return "已保存，可按关键词查找 · 正在准备智能检索";
  if (note.retrieval.semantic === "failed") return "已保存，可按关键词查找 · 智能检索更新失败";
  if (note.retrieval.semantic === "requires_rebuild") return "已保存，可按关键词查找 · 智能检索需在设置中重新处理";
  if (note.retrieval.semantic === "pending") return "已保存，可按关键词查找 · 智能检索待更新";
  return note.retrieval.semantic === "ready" ? "已保存，可检索" : "已保存，可按关键词查找";
}

function dateGroup(value: string) {
  const date = new Date(value);
  const now = new Date();
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const days = Math.round((start.getTime() - target.getTime()) / 86_400_000);
  if (days === 0) return "今天";
  if (days === 1) return "昨天";
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

function timeText(value: string) {
  return new Date(value).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
  });
}

export const NotesPage = forwardRef<NotesPageHandle, {
  active?: boolean;
  libraryRevision?: number;
  newRequestKey?: string;
  openNoteId?: string;
  openRequestKey?: string;
  onSaveBlocked?: () => void;
}>(function NotesPage(
  { active = true, libraryRevision = 0, newRequestKey = "", openNoteId = "", openRequestKey = "", onSaveBlocked },
  ref,
) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedNote, setSelectedNote] = useState<Note | undefined>(undefined);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [pageError, setPageError] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Note | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [creating, setCreating] = useState(false);
  const [closing, setClosing] = useState(false);
  const [indexingKey, setIndexingKey] = useState("");
  const titleInput = useRef<HTMLInputElement>(null);
  const bodyInput = useRef<HTMLTextAreaElement>(null);
  const editor = useRef<HTMLElement>(null);
  const selectedRef = useRef<string | null>(null);
  const titleRef = useRef("");
  const contentRef = useRef("");
  const dirtyRef = useRef(false);
  const revisionRef = useRef(0);
  const savePromise = useRef<Promise<void> | null>(null);
  const handledNew = useRef(new Set<string>());
  const handledOpen = useRef(new Set<string>());
  const createdDrafts = useRef(new Set<string>());
  const creatingRef = useRef(false);
  const restored = useRef(false);
  const scrollPositions = useRef<Record<string, { body: number; editor: number }>>({});
  const indexTimer = useRef<number | undefined>(undefined);
  const pendingIndex = useRef<Note | null>(null);
  const deleteDialog = useRef<HTMLDivElement>(null);
  const saveBlockedRef = useRef(onSaveBlocked);
  const activeRef = useRef(active);
  activeRef.current = active;
  saveBlockedRef.current = onSaveBlocked;
  const notesRef = useRef(notes);
  notesRef.current = notes;

  const rememberScroll = useCallback(() => {
    if (!selectedRef.current || !activeRef.current) return;
    scrollPositions.current[selectedRef.current] = { body: bodyInput.current?.scrollTop ?? 0, editor: editor.current?.scrollTop ?? 0 };
    writePreference(SCROLL_KEY, JSON.stringify(scrollPositions.current));
  }, []);

  selectedRef.current = selectedId;
  titleRef.current = title;
  contentRef.current = content;

  const applySelection = useCallback((note: Note) => {
    selectedRef.current = note.id;
    titleRef.current = note.title;
    contentRef.current = note.content;
    writePreference(LAST_NOTE, note.id);
    setSelectedId(note.id);
    setSelectedNote(note);
    setTitle(note.title);
    setContent(note.content);
    dirtyRef.current = false;
    setSaveState("saved");
    setSaveError("");
  }, []);

  const prepareSearch = useCallback(async (note: Note) => {
    if (isLibraryRelocating()) { pendingIndex.current = note; return; }
    const done = beginLibraryActivity("正在更新笔记智能检索");
    const key = `${note.id}:${note.updatedAt}`;
    setIndexingKey(key);
    try {
      const indexed = await indexNote(note.id, note.updatedAt);
      if (indexed) {
        setNotes((current) => current.map((item) => item.id === indexed.id && item.updatedAt === indexed.updatedAt ? indexed : item));
        setSelectedNote((current) => current?.id === indexed.id && current.updatedAt === indexed.updatedAt ? indexed : current);
      }
    } catch {
      setNotes((current) => current.map((item) => item.id === note.id && item.updatedAt === note.updatedAt && item.retrieval
        ? { ...item, retrieval: { ...item.retrieval, semantic: "failed" } } : item));
      setSelectedNote((current) => current?.id === note.id && current.updatedAt === note.updatedAt && current.retrieval
        ? { ...current, retrieval: { ...current.retrieval, semantic: "failed" } } : current);
    } finally { setIndexingKey((current) => current === key ? "" : current); done(); }
  }, []);

  const scheduleSearch = useCallback((saved: Note) => {
    if (saved.retrieval?.semantic !== "pending" || saved.retrieval.keyword !== "ready") return;
    pendingIndex.current = saved;
    window.clearTimeout(indexTimer.current);
    indexTimer.current = window.setTimeout(() => {
      const pending = pendingIndex.current;
      if (!pending || dirtyRef.current) return;
      pendingIndex.current = null;
      void prepareSearch(pending);
    }, 1400);
  }, [prepareSearch]);

  const persist = useCallback(async () => {
    if (savePromise.current) return savePromise.current;
    const drain = async () => {
      while (dirtyRef.current && selectedRef.current) {
        const noteId = selectedRef.current;
        const revision = revisionRef.current;
        setSaveState("saving");
        setSaveError("");
        try {
          const saved = await updateNote(noteId, titleRef.current, contentRef.current);
          setNotes((current) => current.map((item) => item.id === noteId ? saved : item)
            .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)));
          if (revision === revisionRef.current) {
            setSelectedNote(saved);
            dirtyRef.current = false;
            setSaveState("saved");
            scheduleSearch(saved);
          }
        } catch (reason) {
          dirtyRef.current = true;
          setSaveState("error");
          setSaveError(reason instanceof Error ? reason.message : String(reason));
          throw reason;
        }
      }
    };
    const operation = drain().finally(() => { savePromise.current = null; });
    savePromise.current = operation;
    return operation;
  }, [scheduleSearch]);

  useImperativeHandle(ref, () => ({ flush: async () => { rememberScroll(); await persist(); } }), [persist, rememberScroll]);

  useEffect(() => useLibraryLocationStore.subscribe((state, previous) => {
    if (previous.locked && !state.locked && pendingIndex.current && !dirtyRef.current) {
      const pending = pendingIndex.current; pendingIndex.current = null;
      void prepareSearch(pending);
    }
  }), [prepareSearch]);

  useEffect(() => {
    if (!libraryRevision) return;
    let current = true;
    void listNotes(query).then(async (items) => {
      if (!current) return;
      setNotes(items);
      if (selectedRef.current) {
        const updated = await getNote(selectedRef.current);
        if (current && !dirtyRef.current) applySelection(updated);
      }
    }).catch(() => { if (current) setPageError("资料库位置已重新读取，笔记列表暂未刷新，请稍后重试。"); });
    return () => { current = false; };
  }, [libraryRevision, applySelection]);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setLoading(true);
      void listNotes(query)
        .then((items) => {
          if (cancelled) return;
          setNotes((current) => [...items, ...current.filter((item) => createdDrafts.current.has(item.id) && !items.some((found) => found.id === item.id))]);
          setPageError("");
          if (!query && !restored.current) {
            restored.current = true;
            try { scrollPositions.current = JSON.parse(readPreference(SCROLL_KEY) ?? "{}"); } catch { scrollPositions.current = {}; }
            if (!selectedRef.current && !newRequestKey && !openRequestKey) {
              const note = items.find((item) => item.id === readPreference(LAST_NOTE)) ?? items[0];
              if (note) applySelection(note);
            }
          }
        })
        .catch((reason: unknown) =>
          !cancelled && setPageError(reason instanceof Error ? reason.message : String(reason)),
        )
        .finally(() => { if (!cancelled) setLoading(false); });
    }, query ? 220 : 0);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [query, applySelection, newRequestKey, openRequestKey]);

  useEffect(() => {
    if (!dirtyRef.current || !selectedId) return;
    const timer = window.setTimeout(() => void persist().catch(() => {}), 750);
    return () => window.clearTimeout(timer);
  }, [title, content, selectedId, persist]);

  useLayoutEffect(() => {
    if (!active || !selectedId) return;
    // Restore only after React has committed this note's textarea. Scheduling
    // a frame inside applySelection can run before the asynchronous commit,
    // leaving the body ref empty and permanently missing the restoration.
    const position = scrollPositions.current[selectedId];
    if (bodyInput.current) bodyInput.current.scrollTop = position?.body ?? 0;
    if (editor.current) editor.current.scrollTop = position?.editor ?? 0;
  }, [active, selectedId]);

  useEffect(() => {
    if (!active) void persist().catch(() => {});
  }, [active, persist]);

  useEffect(() => {
    let disposed = false;
    let unlisten: (() => void) | undefined;
    let closePending = false;
    const beforeUnload = (event: BeforeUnloadEvent) => {
      if (dirtyRef.current || savePromise.current) { event.preventDefault(); event.returnValue = ""; }
    };
    window.addEventListener("beforeunload", beforeUnload);
    if (isTauri()) {
      const appWindow = getCurrentWindow();
      void appWindow.onCloseRequested(async (event) => {
        event.preventDefault();
        if (isLibraryRelocating()) return;
        if (closePending) return;
        closePending = true;
        setClosing(true);
        window.clearTimeout(indexTimer.current);
        rememberScroll();
        try { await persist(); await appWindow.destroy(); }
        catch { setClosing(false); closePending = false; saveBlockedRef.current?.(); setPageError("笔记尚未保存，窗口已保留。请重试保存后再关闭。"); }
      }).then((dispose) => { if (disposed) dispose(); else unlisten = dispose; })
        .catch(() => setPageError("无法保护关闭前的保存，请先确认笔记已保存再退出。"));
    }
    return () => {
      disposed = true;
      unlisten?.();
      window.removeEventListener("beforeunload", beforeUnload);
      rememberScroll();
      window.clearTimeout(indexTimer.current);
      void persist().catch(() => {});
    };
  }, [persist, rememberScroll]);

  const newNote = useCallback(async () => {
    if (creatingRef.current) return;
    creatingRef.current = true;
    setCreating(true);
    try {
      rememberScroll();
      await persist();
      const empty = notesRef.current.find((item) => createdDrafts.current.has(item.id) && !item.title.trim() && !item.content.trim());
      if (empty) { applySelection(empty); titleInput.current?.focus(); return; }
      const note = await createNote();
      createdDrafts.current.add(note.id);
      setNotes((current) => [note, ...current]);
      applySelection(note);
      requestAnimationFrame(() => titleInput.current?.focus());
    } catch (reason) {
      setPageError(reason instanceof Error ? reason.message : String(reason));
    } finally { creatingRef.current = false; setCreating(false); }
  }, [applySelection, persist, rememberScroll]);

  useEffect(() => {
    if (!active || !newRequestKey || handledNew.current.has(newRequestKey)) return;
    handledNew.current.add(newRequestKey);
    void newNote();
  }, [active, newRequestKey, newNote]);

  useEffect(() => {
    if (!active || !openNoteId || !openRequestKey || handledOpen.current.has(openRequestKey)) return;
    handledOpen.current.add(openRequestKey);
    void (async () => {
      try {
        rememberScroll();
        await persist();
        const cached = notes.find((note) => note.id === openNoteId);
        applySelection(cached ?? (await getNote(openNoteId)));
      } catch (reason) {
        setPageError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
  }, [active, applySelection, notes, openNoteId, openRequestKey, persist, rememberScroll]);

  const select = async (note: Note) => {
    if (note.id === selectedRef.current) return;
    try {
      rememberScroll();
      await persist();
      applySelection(note);
    } catch {
      // Keep the current note visible so unsaved text is never silently abandoned.
    }
  };

  const edit = (field: "title" | "content", value: string) => {
    revisionRef.current += 1;
    dirtyRef.current = true;
    setSaveState("saving");
    setSaveError("");
    if (field === "title") { titleRef.current = value; setTitle(value); }
    else { contentRef.current = value; setContent(value); }
  };

  const remove = async () => {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    setPageError("");
    try {
      await persist();
      await deleteNote(deleteTarget.id);
      createdDrafts.current.delete(deleteTarget.id);
      setNotes((current) => current.filter((note) => note.id !== deleteTarget.id));
      if (selectedRef.current === deleteTarget.id) {
        const next = notesRef.current.find((item) => item.id !== deleteTarget.id);
        selectedRef.current = null;
        setSelectedId(null);
        setSelectedNote(undefined);
        setTitle("");
        setContent("");
        dirtyRef.current = false;
        setSaveState("idle");
        if (next) applySelection(next);
        else writePreference(LAST_NOTE, "");
      }
      setDeleteTarget(null);
    } catch (reason) {
      setPageError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setDeleting(false);
    }
  };

  useEffect(() => {
    if (!deleteTarget) return;
    const previous = document.activeElement as HTMLElement | null;
    deleteDialog.current?.querySelector<HTMLButtonElement>("button")?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, [deleteTarget]);

  let lastGroup = "";
  const selectedIndexing = indexingKey === `${selectedNote?.id}:${selectedNote?.updatedAt}`;
  return (
    <section className="notes-workspace" aria-label="笔记工作区">
      <aside className="notes-sidebar">
        <div className="notes-sidebar-header">
          <div>
            <p>个人记忆</p>
            <h1>笔记</h1>
          </div>
          <Button disabled={creating || closing} onClick={() => void newNote()}>
            <Plus size={15} />
            记一下
          </Button>
        </div>
        <label className="notes-search">
          <Search size={15} />
          <input
            aria-label="搜索笔记"
            placeholder="搜索笔记……"
            maxLength={200}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          {query && (
            <button aria-label="清空笔记搜索" onClick={() => setQuery("")}>
              <X size={14} />
            </button>
          )}
        </label>
        <div className="notes-list">
          {notes.map((note) => {
            const group = dateGroup(note.updatedAt);
            const showGroup = group !== lastGroup;
            lastGroup = group;
            return (
              <div key={note.id}>
                {showGroup && <p className="notes-group">{group}</p>}
                <button
                  className={`notes-list-item ${selectedId === note.id ? "selected" : ""}`}
                  title={note.displayTitle}
                  aria-current={selectedId === note.id ? "page" : undefined}
                  disabled={closing}
                  onClick={() => void select(note)}
                >
                  <span>{note.displayTitle}</span>
                  <small>{timeText(note.updatedAt)}</small>
                </button>
              </div>
            );
          })}
          {!loading && !notes.length && (
            <p className="notes-empty-list">
              {query ? "没有匹配的笔记" : "写下的内容会出现在这里"}
            </p>
          )}
          {loading && <p className="notes-empty-list">正在读取笔记…</p>}
        </div>
      </aside>

      <main className="notes-editor" ref={editor} onScroll={rememberScroll}>
        {pageError && <div className="notes-page-error" role="alert">{pageError}</div>}
        {selectedId ? (
          <div className="notes-sheet">
            <header className="notes-editor-header">
              <div className="notes-save-state" role="status">
                {closing ? "正在保存后关闭…" : saveState === "saving" && "正在保存…"}
                {!closing && saveState === "saved" && retrievalLabel(selectedNote, selectedIndexing)}
                {saveState === "error" && "保存失败"}
              </div>
              <button
                className="notes-delete"
                aria-label="删除笔记"
                title="删除笔记"
                disabled={closing}
                onClick={() => {
                  const note = notes.find((item) => item.id === selectedId);
                  if (note) setDeleteTarget(note);
                }}
              >
                <Trash2 size={16} />
              </button>
            </header>
            <input
              ref={titleInput}
              className="notes-title"
              aria-label="笔记标题"
              placeholder="标题（可选）"
              maxLength={200}
              value={title}
              disabled={closing}
              onChange={(event) => edit("title", event.target.value)}
            />
            <textarea
              ref={bodyInput}
              className="notes-body"
              aria-label="笔记正文"
              placeholder="正文……"
              maxLength={200_000}
              value={content}
              disabled={closing}
              onScroll={rememberScroll}
              onChange={(event) => edit("content", event.target.value)}
            />
            {saveError && <div className="notes-save-error" role="alert"><p>{saveError} 编辑内容仍保留在这里。</p><Button variant="secondary" onClick={() => void persist().catch(() => {})}>重试保存</Button></div>}
            {saveState === "saved" && !selectedIndexing && selectedNote?.retrieval && ["pending", "failed"].includes(selectedNote.retrieval.semantic ?? "") && (
              <div className="notes-index-retry"><Button variant="secondary" onClick={() => void prepareSearch(selectedNote)}>重试智能检索</Button></div>
            )}
          </div>
        ) : (
          <div className="notes-welcome">
            <NotebookPen size={26} />
            <h2>{loading ? "正在读取笔记…" : notes.length ? "继续写下你的想法" : "记下此刻值得留下的东西"}</h2>
            <p>{notes.length ? "从左侧选择一条笔记，或记下新的内容。" : "一段想法、一次决定，或日后可能需要找回的细节。"}</p>
            <Button disabled={creating || closing} onClick={() => void newNote()}>
              <Plus size={16} />
              记一下
            </Button>
          </div>
        )}
      </main>

      {deleteTarget && (
        <div className="notes-dialog-backdrop">
          <div ref={deleteDialog} className="notes-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-note-title" onKeyDown={(event) => {
            if (event.key === "Escape" && !deleting) setDeleteTarget(null);
            if (event.key !== "Tab") return;
            const buttons = Array.from(deleteDialog.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? []);
            const first = buttons[0], last = buttons[buttons.length - 1];
            if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
            else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
          }}>
            <h2 id="delete-note-title">删除这条笔记？</h2>
            <p>删除后无法恢复，它也不会再出现在搜索和 AI 回答中。</p>
            <div>
              <Button variant="secondary" disabled={deleting} onClick={() => setDeleteTarget(null)}>取消</Button>
              <button className="notes-danger" disabled={deleting} onClick={() => void remove()}>
                {deleting ? "正在删除…" : "删除"}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
});
