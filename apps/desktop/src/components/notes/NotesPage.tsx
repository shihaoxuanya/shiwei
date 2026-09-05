import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { NotebookPen, Plus, Search, Trash2, X } from "lucide-react";
import { Button } from "../ui/button";
import {
  createNote,
  deleteNote,
  getNote,
  listNotes,
  updateNote,
  type Note,
} from "../../lib/notes";
import "./notes.css";

export type NotesPageHandle = {
  flush: () => Promise<void>;
};

type SaveState = "idle" | "saving" | "saved" | "error";

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
  newRequestKey?: string;
  openNoteId?: string;
  openRequestKey?: string;
}>(function NotesPage(
  { active = true, newRequestKey = "", openNoteId = "", openRequestKey = "" },
  ref,
) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [pageError, setPageError] = useState("");
  const [saveState, setSaveState] = useState<SaveState>("idle");
  const [saveError, setSaveError] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Note | null>(null);
  const [deleting, setDeleting] = useState(false);
  const titleInput = useRef<HTMLInputElement>(null);
  const selectedRef = useRef<string | null>(null);
  const titleRef = useRef("");
  const contentRef = useRef("");
  const dirtyRef = useRef(false);
  const revisionRef = useRef(0);
  const savePromise = useRef<Promise<void> | null>(null);
  const handledNew = useRef(new Set<string>());
  const handledOpen = useRef(new Set<string>());

  selectedRef.current = selectedId;
  titleRef.current = title;
  contentRef.current = content;

  const applySelection = useCallback((note: Note) => {
    setSelectedId(note.id);
    setTitle(note.title);
    setContent(note.content);
    dirtyRef.current = false;
    setSaveState("saved");
    setSaveError("");
  }, []);

  const persist = useCallback(async () => {
    if (savePromise.current) await savePromise.current;
    const noteId = selectedRef.current;
    if (!noteId || !dirtyRef.current) return;
    const savedTitle = titleRef.current;
    const savedContent = contentRef.current;
    const revision = revisionRef.current;
    setSaveState("saving");
    setSaveError("");
    const operation = updateNote(noteId, savedTitle, savedContent)
      .then((saved) => {
        setNotes((current) => {
          const next = current.map((item) =>
            item.id === noteId
              ? revision === revisionRef.current
                ? saved
                : {
                    ...saved,
                    title: titleRef.current,
                    content: contentRef.current,
                    displayTitle:
                      titleRef.current.trim() ||
                      contentRef.current.split("\n").find((line) => line.trim())?.trim().slice(0, 32) ||
                      "无标题笔记",
                  }
              : item,
          );
          return next.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
        });
        if (revision === revisionRef.current) {
          dirtyRef.current = false;
          setSaveState("saved");
        }
      })
      .catch((reason: unknown) => {
        dirtyRef.current = true;
        setSaveState("error");
        setSaveError(reason instanceof Error ? reason.message : String(reason));
        throw reason;
      })
      .finally(() => {
        savePromise.current = null;
      });
    savePromise.current = operation;
    await operation;
  }, []);

  useImperativeHandle(ref, () => ({ flush: persist }), [persist]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setLoading(true);
      void listNotes(query)
        .then((items) => {
          setNotes(items);
          setPageError("");
        })
        .catch((reason: unknown) =>
          setPageError(reason instanceof Error ? reason.message : String(reason)),
        )
        .finally(() => setLoading(false));
    }, query ? 220 : 0);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    if (!dirtyRef.current || !selectedId) return;
    const timer = window.setTimeout(() => void persist().catch(() => {}), 750);
    return () => window.clearTimeout(timer);
  }, [title, content, selectedId, persist]);

  useEffect(() => {
    if (!active) void persist().catch(() => {});
  }, [active, persist]);

  const newNote = useCallback(async () => {
    try {
      await persist();
      const note = await createNote();
      setNotes((current) => [note, ...current]);
      applySelection(note);
      requestAnimationFrame(() => titleInput.current?.focus());
    } catch (reason) {
      setPageError(reason instanceof Error ? reason.message : String(reason));
    }
  }, [applySelection, persist]);

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
        await persist();
        const cached = notes.find((note) => note.id === openNoteId);
        applySelection(cached ?? (await getNote(openNoteId)));
      } catch (reason) {
        setPageError(reason instanceof Error ? reason.message : String(reason));
      }
    })();
  }, [active, applySelection, notes, openNoteId, openRequestKey, persist]);

  const select = async (note: Note) => {
    if (note.id === selectedRef.current) return;
    try {
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
    if (field === "title") setTitle(value);
    else setContent(value);
  };

  const remove = async () => {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    setPageError("");
    try {
      await deleteNote(deleteTarget.id);
      setNotes((current) => current.filter((note) => note.id !== deleteTarget.id));
      if (selectedRef.current === deleteTarget.id) {
        setSelectedId(null);
        setTitle("");
        setContent("");
        dirtyRef.current = false;
        setSaveState("idle");
      }
      setDeleteTarget(null);
    } catch (reason) {
      setPageError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setDeleting(false);
    }
  };

  let lastGroup = "";
  return (
    <section className="notes-workspace" aria-label="笔记工作区">
      <aside className="notes-sidebar">
        <div className="notes-sidebar-header">
          <div>
            <p>个人记忆</p>
            <h1>笔记</h1>
          </div>
          <Button onClick={() => void newNote()}>
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

      <main className="notes-editor">
        {pageError && <div className="notes-page-error" role="alert">{pageError}</div>}
        {selectedId ? (
          <div className="notes-sheet">
            <header className="notes-editor-header">
              <div className="notes-save-state" role="status">
                {saveState === "saving" && "正在保存…"}
                {saveState === "saved" && "已保存"}
                {saveState === "error" && "保存失败"}
              </div>
              <button
                className="notes-delete"
                aria-label="删除笔记"
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
              onChange={(event) => edit("title", event.target.value)}
            />
            <textarea
              className="notes-body"
              aria-label="笔记正文"
              placeholder="正文……"
              maxLength={200_000}
              value={content}
              onChange={(event) => edit("content", event.target.value)}
            />
            {saveError && <p className="notes-save-error" role="alert">{saveError}</p>}
          </div>
        ) : (
          <div className="notes-welcome">
            <NotebookPen size={26} />
            <h2>记下此刻值得留下的东西</h2>
            <p>一段想法、一次决定，或日后可能需要找回的细节。</p>
            <Button onClick={() => void newNote()}>
              <Plus size={16} />
              记一下
            </Button>
          </div>
        )}
      </main>

      {deleteTarget && (
        <div className="notes-dialog-backdrop">
          <div className="notes-dialog" role="dialog" aria-modal="true" aria-labelledby="delete-note-title">
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
