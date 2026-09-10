import { useEffect, useRef, useState } from "react";
import { ExternalLink, X } from "lucide-react";
import { getWebSnapshot, openWebSource, type WebSnapshot } from "../lib/import";
import { useImportStore } from "../stores/import-store";
import { useLibraryLocationStore } from "../lib/library-location";
import { Button } from "./ui/button";

const message = (error: unknown) => error instanceof Error ? error.message : String(error);

export function AddLinkDialog({ onClose }: { onClose: () => void }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const { addUrl, status } = useImportStore();
  const locked = useLibraryLocationStore(s => s.locked);
  useEffect(() => {
    const trigger = document.activeElement as HTMLElement | null;
    input.current?.focus();
    return () => { if (trigger?.isConnected) trigger.focus(); };
  }, []);
  const submit = () => {
    try {
      const parsed = new URL(url.trim());
      if (!["http:", "https:"].includes(parsed.protocol) || parsed.username || parsed.password) throw new Error();
    } catch { setError("请输入完整的公开网页地址（http:// 或 https://），不要包含账号密码。"); return; }
    void addUrl(url.trim());
    onClose();
  };
  return <div className="web-dialog-backdrop"><div ref={dialog} className="web-link-dialog" role="dialog" aria-modal="true" aria-labelledby="add-link-title" onKeyDown={event => {
    if (event.key === "Escape") { event.preventDefault(); onClose(); }
    if (event.key === "Tab") {
      const controls = Array.from(dialog.current?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled)") ?? []);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  }}>
    <h2 id="add-link-title">添加链接</h2>
    <p className="muted">保存公开网页正文到本机，之后可离线阅读和提问。不支持登录页面、纯动态网页或文件下载链接。</p>
    <form onSubmit={event => { event.preventDefault(); submit(); }}>
      <label htmlFor="web-source-url">网页地址</label>
      <input ref={input} id="web-source-url" type="text" inputMode="url" autoComplete="off" maxLength={8192} placeholder="https://example.com/article" value={url} onChange={event => { setUrl(event.target.value); setError(""); }} aria-invalid={!!error} aria-describedby="web-import-disclosure" />
      {error && <p role="alert" className="error-text">{error}</p>}
      <p id="web-import-disclosure" className="muted">仅访问你添加的页面，不使用浏览器登录状态。启用在线智能检索时，保存的网页正文会发送给你选择的模型服务建立索引。</p>
      <div className="action-row"><Button type="button" variant="secondary" onClick={onClose}>取消</Button><Button type="submit" disabled={!url.trim() || locked || status === "importing" || status === "selecting"}>保存网页</Button></div>
    </form>
  </div></div>;
}

export function WebSnapshotContent({ sourceId, focusChunkId, headingPath, snippet }: { sourceId: string; focusChunkId?: string; headingPath?: string; snippet?: string }) {
  const [snapshot, setSnapshot] = useState<WebSnapshot | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const selected = useRef<HTMLElement>(null);
  useEffect(() => {
    let current = true; setSnapshot(null); setError("");
    void getWebSnapshot(sourceId).then(value => { if (current) setSnapshot(value); }).catch(e => { if (current) setError(message(e)); });
    return () => { current = false; };
  }, [sourceId, retry]);
  useEffect(() => { selected.current?.scrollIntoView?.({ block: "nearest" }); }, [snapshot, focusChunkId]);
  if (error) return <div role="alert"><p>{error}</p><Button variant="secondary" onClick={() => setRetry(n => n + 1)}>重试读取</Button></div>;
  if (!snapshot) return <p role="status">正在读取已保存的网页…</p>;
  const exact = snapshot.chunks?.find(chunk => chunk.chunkId === focusChunkId)?.sectionIndex;
  const fallback = snapshot.sections.findIndex(section => (headingPath && section.headingPath?.join(" > ") === headingPath) || (snippet && section.blocks.some(block => block.text.includes(snippet.slice(0, 80)))));
  const focus = exact ?? (fallback >= 0 ? fallback : undefined);
  return <div className="web-snapshot-content">
    <div className="web-snapshot-meta"><p>类型：网页快照</p><p>标题：{snapshot.title}</p><p>原网址：{snapshot.originalUrl}</p>{snapshot.finalUrl !== snapshot.originalUrl && <p>实际网址：{snapshot.finalUrl}</p>}<p>保存于：{new Date(snapshot.capturedAt).toLocaleString("zh-CN")}</p><p>以下是本机保存的正文，不加载原网页或远程资源。</p></div>
    {snapshot.sections.map((section, index) => <section key={index} ref={index === focus ? selected : undefined} className={index === focus ? "web-snapshot-section selected-evidence" : "web-snapshot-section"} aria-label={index === focus ? "引用对应段落" : undefined}>
      {section.blocks.map((block, blockIndex) => block.kind === "heading" ? <h3 key={blockIndex}>{block.text}</h3> : block.kind === "code" ? <pre key={blockIndex}><code>{block.text}</code></pre> : <p key={blockIndex}>{block.text}</p>)}
    </section>)}
  </div>;
}

export function WebSnapshotDrawer({ sourceId, title, onClose }: { sourceId: string; title: string; onClose: () => void }) {
  const close = useRef<HTMLButtonElement>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const trigger = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.preventDefault(); onClose(); } };
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("keydown", escape); if (trigger?.isConnected) trigger.focus({ preventScroll: true }); };
  }, [onClose]);
  return <aside className="web-snapshot-drawer" aria-label="已保存的网页"><header><h2>{title}</h2><button ref={close} onClick={onClose} className="icon-button" aria-label="关闭网页快照"><X size={18}/></button></header><div className="web-snapshot-scroll"><WebSnapshotContent sourceId={sourceId}/></div><footer>{error && <p role="alert" className="error-text">{error}</p>}<Button variant="secondary" onClick={() => void openWebSource(sourceId).catch(e => setError(message(e)))}><ExternalLink size={15}/>打开原网页</Button><p className="muted">打开原网页将使用系统浏览器联网访问。</p></footer></aside>;
}
