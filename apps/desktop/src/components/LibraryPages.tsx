import { useEffect, useRef, useState, type ReactNode } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { FilePlus2, FolderPlus, NotebookPen, Search, FileText, ArrowUpRight, MoreHorizontal, RefreshCw, ShieldCheck } from "lucide-react";
import { Button } from "./ui/button";
import { deleteSource, openSource, reindexSource, revealSource, type SourceSummary } from "../lib/import";
import { searchLocal, type LexicalHit } from "../lib/search";
import type { Note } from "../lib/notes";
import { useImportStore } from "../stores/import-store";
import { useWorkerStore } from "../stores/worker-store";

export const privacySummary = "资料和笔记默认保存在本机。使用在线 AI 时，问答或智能检索所需文本会发送给你选择的模型服务。";
const readableError = (e: unknown) => e instanceof Error ? e.message : String(e);
export function sourceStatus(source: SourceSummary): string {
  if(source.status === "failed") return "处理失败";
  if(source.status === "processing") return "正在处理";
  if(source.status !== "searchable") return "已保存，等待处理";
  if(source.retrieval?.semantic === "ready") return "已就绪";
  if(source.retrieval?.semantic === "failed") return "可按关键词查找 · 智能检索失败";
  if(source.retrieval?.semantic === "requires_rebuild") return "可按关键词查找 · 智能检索待更新";
  if(source.retrieval?.semantic === "pending") return "可按关键词查找 · 智能检索未就绪";
  return "可按关键词查找";
}
function formatBytes(n: number) { return n < 1024 ? `${n} B` : n < 1048576 ? `${(n/1024).toFixed(1)} KB` : `${(n/1048576).toFixed(1)} MB`; }
function fileInfo(s:SourceSummary) { return `${(s.filename.split('.').pop() ?? '文件').toUpperCase()} · ${new Date(s.importedAt).toLocaleDateString('zh-CN')} · ${formatBytes(s.size)}`; }

export function ImportActions({children}: {children?:ReactNode}) {
  const {status,addFiles,addFolder} = useImportStore();
  const disabled = status === "importing" || status === "selecting";
  return <div className="action-row"><Button disabled={disabled} onClick={()=>void addFiles()}><FilePlus2 size={16}/>添加文件</Button><Button variant="secondary" disabled={disabled} onClick={()=>void addFolder()}><FolderPlus size={16}/>添加文件夹</Button>{children}</div>;
}
export function ImportFeedback() {
  const state = useImportStore();
  if(state.status === "idle") return null;
  return <div className={`status-box ${state.status === "error" ? "error-box" : ""}`} role={state.status === "error" ? "alert" : "status"}>
    {state.status === "selecting" && "请选择要添加的资料。"}
    {state.status === "importing" && <><p>{state.progress?.currentStep || "正在读取资料…"}</p><progress max={1} value={state.progress?.currentStep?.includes("智能检索") ? undefined : state.progress?.progress ?? 0} aria-label="资料处理进度"/>{state.progress?.filename && <p className="muted truncate">{state.progress.filename}</p>}</>}
    {state.status === "error" && <p>{state.error}</p>}
    {state.status === "success" && state.report && <><p>已处理 {state.report.summary.imported} 份资料{state.report.summary.skipped > 0 && `，跳过 ${state.report.summary.skipped} 份重复资料`}{state.report.summary.failed > 0 && `，${state.report.summary.failed} 份处理失败`}。</p>
      {state.report.embeddingIndex?.status === "failed" && <p className="error-text">资料已保存，可按关键词查找；智能检索更新失败，可到设置重试。</p>}
      {state.report.embeddingIndex?.requiresRebuild && <p>可按关键词查找；智能检索服务已变更，请到设置确认后更新。</p>}
      {state.report.failed.map(item=><div className="failed-import" key={item.path}><div><strong>{item.path.split(/[\\/]/).pop()}</strong><p>{item.reason}</p></div><Button variant="secondary" onClick={()=>void state.addPaths([item.path])}>重试</Button></div>)}
    </>}
  </div>;
}

type DataProps = {sources:SourceSummary[];loading:boolean;error:string;onRefresh:()=>void};
export function HomePage({sources,notes,loading,error,onNewNote,onOpenNote,onPrivacy,onRefresh}:DataProps & {notes:Note[];onNewNote:()=>void;onOpenNote:(id:string)=>void;onPrivacy:()=>void}) {
  const {status,check} = useWorkerStore();
  const {addPaths} = useImportStore();
  const [dragging,setDragging] = useState(false);
  const [operationError,setOperationError] = useState("");
  const hasContent = sources.length + notes.length > 0;
  useEffect(()=>{
    if(!isTauri()) return;
    let disposed = false; let unlisten:(()=>void)|undefined;
    void getCurrentWebview().onDragDropEvent(event=>{
      setDragging(event.payload.type === "enter" || event.payload.type === "over");
      if(event.payload.type === "drop") void addPaths(event.payload.paths);
    }).then(fn=>{if(disposed)fn();else unlisten=fn;});
    return()=>{disposed=true;unlisten?.();};
  },[addPaths]);
  const recent = [
    ...sources.map(source=>({key:source.id,title:source.filename,date:source.importedAt,kind:"资料",state:sourceStatus(source),open:()=>openSource(source.originalPath).catch(()=>openSource(source.storedPath))})),
    ...notes.map(note=>({key:note.id,title:note.displayTitle,date:note.updatedAt,kind:"笔记",state:note.retrieval?.keyword === "ready" ? "已保存，可检索" : "已保存",open:async()=>onOpenNote(note.id)})),
  ].sort((a,b)=>b.date.localeCompare(a.date)).slice(0,5);
  return <main className={`content-page home-page ${hasContent ? "daily-home" : "empty-home"}`}>
    <header className="page-header"><div><p className="eyebrow">个人 AI 记忆工具</p><h1 className={hasContent ? "" : "welcome-title"}>{hasContent ? "把值得留下的，交给拾微。" : <>你不用整理，<br/>它替你记住。</>}</h1><p className="page-description">{hasContent ? "添加新资料，或记下此刻的想法。需要时，在对话中找回来。" : "把看过的资料和自己的记录留在这里，以后用一句话找回来。"}</p></div></header>
    {status === "error" && <div className="status-box error-box" role="alert"><p>本地服务暂时不可用，资料处理已暂停。</p><Button variant="secondary" onClick={()=>void check()}>重新连接</Button></div>}
    {(error || operationError) && <div role="alert" className="status-box error-box">{error || operationError}<Button variant="ghost" onClick={onRefresh}>重试读取</Button></div>}
    <section className={`home-inputs ${dragging ? "dragging" : ""}`} aria-label="添加新信息">
      <div className="import-entry"><FolderPlus size={26}/><h2>添加资料</h2><p>把文件或文件夹拖到这里</p><ImportActions/><p className="format-help">PDF、Word、PPT、Excel、图片与文本<br/>单文件不超过 100MB；PDF 不超过 300 页。</p></div>
      <div className="note-entry"><NotebookPen size={26}/><h2>记一下</h2><p>一个想法、一次讨论，<br/>或日后需要找回的细节。</p><Button variant="secondary" onClick={onNewNote}>写一条笔记</Button><p className="format-help">自动保存在本机，随时可以继续写。</p></div>
    </section>
    <ImportFeedback/>
    {sources.some(s=>s.status === "failed") && <section className="retry-list" aria-label="需要处理的资料"><h2>需要再处理一下</h2>{sources.filter(s=>s.status === "failed").slice(0,5).map(s=><div className="failed-import" key={s.id}><div><strong>{s.filename}</strong><p>{s.error || "资料处理失败"}</p></div><Button variant="secondary" onClick={()=>void reindexSource(s.id).then(onRefresh).catch(e=>setOperationError(readableError(e)))}>重试</Button></div>)}</section>}
    {loading && !hasContent && <p className="muted" role="status">正在读取本地内容…</p>}
    {hasContent && <section className="recent-section"><h2>最近留下的</h2><div className="file-list">{recent.map(item=><button key={item.key} className="recent-item" title={item.title} onClick={()=>void item.open().catch(e=>setOperationError(readableError(e)))}>{item.kind === "笔记" ? <NotebookPen size={18}/> : <FileText size={18}/>}<span className="min-w-0"><strong className="truncate block">{item.title}</strong><small>{item.kind} · {new Date(item.date).toLocaleDateString('zh-CN')} · {item.state}</small></span><ArrowUpRight size={16}/></button>)}</div></section>}
    <div className="home-privacy"><ShieldCheck size={18}/><div><p>{privacySummary}</p><button onClick={onPrivacy}>了解数据如何使用</button></div></div>
  </main>;
}

function SourceMenu({source,busy,onReindex,onDelete,onError}: {source:SourceSummary;busy:boolean;onReindex:()=>void;onDelete:()=>void;onError:(e:unknown)=>void}) {
  const [open,setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null); const button=useRef<HTMLButtonElement>(null);
  useEffect(()=>{if(!open)return;const close=(e:PointerEvent)=>{if(!ref.current?.contains(e.target as Node))setOpen(false);};document.addEventListener('pointerdown',close);return()=>document.removeEventListener('pointerdown',close);},[open]);
  return <div className="source-menu" ref={ref} onKeyDown={e=>{if(e.key==='Escape'){setOpen(false);button.current?.focus();}}}>
    <button ref={button} className="icon-button" aria-label={`${source.filename}的更多操作`} title="更多操作" aria-expanded={open} onClick={()=>setOpen(!open)}><MoreHorizontal size={18}/></button>
    {open && <div className="menu-popover"><p className="path-detail" title={source.originalPath}>{source.originalPath}</p><button onClick={()=>{setOpen(false);void revealSource(source.originalPath).catch(()=>revealSource(source.storedPath)).catch(onError);}}>在文件管理器中显示</button><button disabled={busy} onClick={()=>{setOpen(false);onReindex();}}>重新处理</button><button className="error-text" disabled={busy} onClick={()=>{setOpen(false);onDelete();}}>删除导入副本</button></div>}
  </div>;
}
export function LibraryPage({sources,loading,error,onRefresh}:DataProps) {
  const [query,setQuery] = useState(""); const [hits,setHits] = useState<LexicalHit[]>([]);
  const [searching,setSearching] = useState(false); const [searchError,setSearchError] = useState("");
  const [operationError,setOperationError] = useState(""); const [busy,setBusy] = useState<string|null>(null);
  useEffect(()=>{let current=true; const q=query.trim(); setHits([]);setSearchError("");if(!q){setSearching(false);return;}setSearching(true); const timer=setTimeout(()=>{void searchLocal(q,100,"imported_file").then(h=>{if(current)setHits(h);}).catch(e=>{if(current)setSearchError(readableError(e));}).finally(()=>{if(current)setSearching(false);});},220);return()=>{current=false;clearTimeout(timer);};},[query,sources]);
  const q=query.trim().toLocaleLowerCase();
  const grouped=new Map<string,LexicalHit[]>();
  for(const hit of hits) { if(!hit.sourceId)continue; const list=grouped.get(hit.sourceId)??[];if(list.length<2 && !list.some(h=>h.chunkId===hit.chunkId))list.push(hit);grouped.set(hit.sourceId,list); }
  const visible=sources.filter(s=>!q || s.filename.toLocaleLowerCase().includes(q) || grouped.has(s.id));
  const action=async(s:SourceSummary,remove=false)=>{
    if(remove && !window.confirm(`删除“${s.filename}”在拾微中的副本及索引？原始位置的文件不会删除。`))return;
    setBusy(s.id);setOperationError("");try{if(remove)await deleteSource(s.id);else await reindexSource(s.id);onRefresh();}catch(e){setOperationError(readableError(e));}finally{setBusy(null);}
  };
  return <section className="content-page library-page"><header className="page-header"><div><p className="eyebrow">本地资料库</p><h1>资料</h1><p className="page-description">{sources.length} 份资料 · 按名称或正文中的词找回来</p></div><div aria-label="添加资料"><ImportActions/></div></header>
    <ImportFeedback/>
    {(error||operationError||searchError) && <p className="status-box error-box" role="alert">{error||operationError||searchError}</p>}
    <label className="library-search"><Search size={18}/><input aria-label="搜索资料名称或正文" value={query} onChange={e=>setQuery(e.target.value)} placeholder="搜索资料名称或正文中的词"/>{searching && <RefreshCw className="animate-spin" size={16}/>}</label>
    {sources.some(s=>s.status !== 'searchable') && <p className="search-scope">仍在处理或处理失败的资料，目前只能按名称查找。</p>}
    <div className="file-list">{loading && sources.length===0 ? <p className="empty-state" role="status">正在读取资料…</p> : searching && visible.length===0 ? <p className="empty-state" role="status">正在查找资料…</p> : visible.length===0 ? <div className="empty-state"><FileText size={28}/><h2>{q ? '没有找到匹配资料' : '把第一份资料留在这里'}</h2><p>{q ? '试试资料名称或正文中的关键词。' : '点击上方添加文件或文件夹，无需先配置 AI。'}</p></div> : visible.map(source=><article key={source.id} className="file-row">
      <FileText size={21} className="file-glyph"/><div className="file-main"><h2 title={source.filename}>{source.filename}</h2><p className="file-meta">{fileInfo(source)}</p><p className={`file-status ${source.status==='failed' ? 'error-text' : ''}`} role={busy===source.id ? 'status' : undefined}>{busy===source.id ? '正在处理…' : sourceStatus(source)}</p>{source.error && <p className="error-text">{source.error}</p>}
      {grouped.get(source.id)?.map(hit=><div key={hit.chunkId} className="search-snippet"><p>{snippet(hit.content,q)}</p>{(hit.pageNumber || hit.sheetName || hit.slideNumber || hit.headingPath) && <small>{hit.pageNumber ? `第 ${hit.pageNumber} 页` : hit.sheetName ? `工作表 ${hit.sheetName}` : hit.slideNumber ? `第 ${hit.slideNumber} 张幻灯片` : hit.headingPath}</small>}</div>)}</div>
      <div className="file-actions"><button className="icon-button" title="打开资料" aria-label={`打开${source.filename}`} onClick={()=>void openSource(source.originalPath).catch(()=>openSource(source.storedPath)).catch(e=>setOperationError(readableError(e)))}><ArrowUpRight size={19}/></button><SourceMenu source={source} busy={busy===source.id} onReindex={()=>void action(source)} onDelete={()=>void action(source,true)} onError={e=>setOperationError(readableError(e))}/></div>
    </article>)}</div>
  </section>;
}
export function snippet(content:string, query:string) {
  const index=content.toLocaleLowerCase().indexOf(query); const start=Math.max(0,index-60); const text=content.slice(start,start+220);
  if(index<0 || !query)return text;
  const at=index-start;
  return <>{start>0&&'…'}{text.slice(0,at)}<mark>{text.slice(at,at+query.length)}</mark>{text.slice(at+query.length)}{start+220<content.length&&'…'}</>;
}
