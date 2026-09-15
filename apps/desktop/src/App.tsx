import { Archive, Home, MessageSquareText, Settings, NotebookPen, CircleCheck, CircleAlert } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { ChatPage } from "./components/chat/ChatPage";
export { ChatPage } from "./components/chat/ChatPage";
export { CitationChip, SourceMatchCard } from "./components/chat/Sources";
import { SettingsPage } from "./components/SettingsPage";
import { HomePage, LibraryPage } from "./components/LibraryPages";
import { useWorkerStore } from "./stores/worker-store";
import { useImportStore } from "./stores/import-store";
import { NotesPage, type NotesPageHandle } from "./components/notes/NotesPage";
import { listSources, type SourceSummary } from "./lib/import";
import { listNotes, type Note } from "./lib/notes";
import { cn } from "./lib/cn";
import { isLibraryRelocating, useLibraryLocationStore } from "./lib/library-location";

const navigation = [
  { id: "home", label: "首页", icon: Home },
  { id: "chat", label: "对话", icon: MessageSquareText },
  { id: "library", label: "资料", icon: Archive },
  { id: "notes", label: "笔记", icon: NotebookPen },
  { id: "settings", label: "设置", icon: Settings },
] as const;
type PageId = (typeof navigation)[number]["id"];

export default function App() {
  const [page, setPage] = useState<PageId>("home");
  const [visited, setVisited] = useState(new Set<PageId>());
  const [newNoteRequest, setNewNoteRequest] = useState("");
  const [openNoteRequest, setOpenNoteRequest] = useState({ id: "", key: "" });
  const [settingsRequest, setSettingsRequest] = useState<{section: "ai"|"privacy"|"about"; key: string}>({section:"ai",key:""});
  const [sources, setSources] = useState<SourceSummary[]>([]);
  const [notes, setNotes] = useState<Note[]>([]);
  const [contentError, setContentError] = useState("");
  const [refreshKey, refresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const report = useImportStore(s => s.report);
  const { status, check } = useWorkerStore();
  const notesRef = useRef<NotesPageHandle>(null);
  const navigationRequest = useRef(0);
  const { locked: relocating, revision: libraryRevision } = useLibraryLocationStore();
  useEffect(() => { if (relocating) navigationRequest.current += 1; }, [relocating]);
  useEffect(() => { void check(); }, [check]);
  useEffect(() => {
    if (relocating) return;
    let current = true;
    setLoading(true);
    void Promise.all([listSources(), listNotes()]).then(([files, memories]) => {
      if (current) { setSources(files); setNotes(memories); setContentError(""); }
    }).catch(e => { if (current) setContentError(String(e)); }).finally(() => { if(current) setLoading(false); });
    return () => { current = false; };
  }, [page, report, refreshKey, relocating, libraryRevision]);
  const navigate = async (destination: PageId) => {
    if (isLibraryRelocating()) return;
    const request = ++navigationRequest.current;
    if (page === "notes" && destination !== "notes") {
      try { await notesRef.current?.flush(); } catch { return; }
    }
    if (request !== navigationRequest.current || isLibraryRelocating()) return;
    setVisited(previous => new Set(previous).add(destination));
    setPage(destination);
  };
  const startNote = () => { if (isLibraryRelocating()) return; navigationRequest.current += 1; setVisited(p => new Set(p).add("notes")); setNewNoteRequest(crypto.randomUUID()); setPage("notes"); };
  const openNote = (id: string) => { if (isLibraryRelocating()) return; navigationRequest.current += 1; setVisited(p => new Set(p).add("notes")); setOpenNoteRequest({ id, key: crypto.randomUUID() }); setPage("notes"); };
  const openSettings = (section: "ai"|"privacy"|"about") => { setSettingsRequest({section,key:crypto.randomUUID()}); void navigate("settings"); };
  const readyItems = [
    ...notes.filter(n => n.retrieval?.keyword === "ready").map(n => ({id:n.id,title:n.displayTitle,kind:"note" as const})),
    ...sources.filter(s => s.status === "searchable").map(s => ({id:s.id,title:s.filename,kind:"file" as const})),
  ];
  return <div className="app-shell" inert={relocating || undefined}>
    <aside className="app-sidebar">
      <button className="brand" onClick={() => void navigate("home")} aria-label="拾微首页">
        <span className="brand-mark">拾</span><span><strong>拾微</strong><small>SHIWEI</small></span>
      </button>
      <nav className="main-navigation" aria-label="主导航">{navigation.map(item => {
        const Icon = item.icon;
        return <button key={item.id} aria-current={page === item.id ? "page" : undefined} className={cn("nav-item", page === item.id && "selected")} onClick={() => void navigate(item.id)}><Icon size={18}/>{item.label}</button>;
      })}</nav>
      <div className="sidebar-footer">
        <button className="service-status" onClick={() => openSettings("about")} title="查看诊断信息">
          {status === "error" ? <CircleAlert size={16}/> : <CircleCheck size={16}/>}
          <span>{status === "connected" ? "本地服务正常" : status === "preview" ? "浏览器预览" : status === "error" ? "本地服务不可用" : "正在连接本地服务"}</span>
        </button>
        <button className="privacy-link" onClick={() => openSettings("privacy")}>本机保存 · 数据如何使用</button>
      </div>
    </aside>
    <div className="app-content">
      {page === "home" && <HomePage sources={sources} notes={notes} loading={loading} error={contentError} onNewNote={startNote} onOpenNote={openNote} onPrivacy={() => openSettings("privacy")} onRefresh={() => refresh(n=>n+1)}/>}
      {visited.has("chat") && <div className="h-full" hidden={page !== "chat"}><ChatPage active={page === "chat"} libraryRevision={libraryRevision} onOpenSettings={() => openSettings("ai")} onOpenNote={openNote} readyItems={readyItems} onAddMaterials={() => void navigate("library")} onCreateNote={startNote}/></div>}
      {page === "library" && <LibraryPage sources={sources} loading={loading} error={contentError} onRefresh={() => refresh(n=>n+1)}/>}
      {visited.has("notes") && <div className="h-full" hidden={page !== "notes"}><NotesPage ref={notesRef} active={page === "notes"} libraryRevision={libraryRevision} newRequestKey={newNoteRequest} openNoteId={openNoteRequest.id} openRequestKey={openNoteRequest.key} onSaveBlocked={()=>{navigationRequest.current += 1;setPage("notes");}}/></div>}
      {visited.has("settings") && <div hidden={page !== "settings"}><SettingsPage initialSection={settingsRequest.section} sectionRequestKey={settingsRequest.key} flushNotes={async () => { try { await notesRef.current?.flush(); } catch (error) { setPage("notes"); throw error; } }}/></div>}
    </div>
  </div>;
}
