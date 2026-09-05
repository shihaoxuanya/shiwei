import { useEffect, useRef, useState } from "react";
import { ExternalLink, FileText, FolderSearch, NotebookPen, X } from "lucide-react";
import {
  openOriginal,
  revealOriginal,
  type Citation,
  type SourceMatch,
} from "../../lib/chat";
import { Button } from "../ui/button";

export function CitationChip({
  citation,
  onSelect,
}: {
  citation: Citation;
  onSelect: (citation: Citation) => void;
}) {
  return (
    <button
      className="chat-citation"
      onClick={() => onSelect(citation)}
      title={
        citation.pageNumber
          ? `第 ${citation.pageNumber} 页 · 查看原文`
          : "查看原文"
      }
    >
      [{citation.citationId}] {citation.sourceFilename}
      {citation.pageNumber && (
        <span className="text-muted"> · 第 {citation.pageNumber} 页</span>
      )}
    </button>
  );
}

export function SourceMatchCard({
  source,
  onChoose,
  disabled = false,
  onOpenNote,
}: {
  source: SourceMatch;
  onChoose: () => void;
  disabled?: boolean;
  onOpenNote?: (noteId: string) => void;
}) {
  const [error, setError] = useState("");
  const open = async (reveal = false) => {
    setError("");
    try {
      await openSourcePath(reveal, source.originalPath, source.storedPath);
    } catch {
      setError("文件暂时无法打开，请检查原件和本地副本是否仍然存在。");
    }
  };
  return (
    <div className="chat-file-card">
      {source.sourceType === "user_note" ? (
        <NotebookPen className="chat-file-icon" size={20} />
      ) : (
        <FileText className="chat-file-icon" size={20} />
      )}
      <div className="min-w-0 flex-1">
        <p className="font-medium text-ink break-words">{source.filename}</p>
        {source.sourceType === "user_note" ? (
          <p className="mt-1 text-xs text-muted">
            笔记 · 更新于 {new Date(source.noteUpdatedAt ?? source.importedAt).toLocaleDateString("zh-CN")}
          </p>
        ) : (
          <p
            className="mt-1 truncate text-xs text-muted"
            title={source.originalPath}
          >
            {source.originalPath}
          </p>
        )}
        <div className="mt-3 flex flex-wrap gap-x-4 gap-y-2 text-xs text-indigo">
          {source.sourceType === "user_note" ? (
            <button
              onClick={() => source.noteId && onOpenNote?.(source.noteId)}
              disabled={!source.noteId || !onOpenNote}
              className="chat-text-action"
            >
              <NotebookPen size={14} />
              打开这条笔记
            </button>
          ) : (
            <>
              <button onClick={() => void open()} className="chat-text-action">
                <ExternalLink size={14} />
                打开原件
              </button>
              <button onClick={() => void open(true)} className="chat-text-action">
                <FolderSearch size={14} />
                定位文件
              </button>
            </>
          )}
          <button
            onClick={onChoose}
            disabled={disabled}
            className="chat-text-action"
          >
            总结这份资料
          </button>
        </div>
        {error && (
          <p role="alert" className="mt-2 text-xs text-red-700">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

async function openSourcePath(
  reveal: boolean,
  original?: string,
  stored?: string,
) {
  const action = reveal ? revealOriginal : openOriginal;
  if (original) {
    try {
      await action(original);
      return;
    } catch (e) {
      if (!stored || original === stored) throw e;
    }
  }
  if (stored) {
    await action(stored);
    return;
  }
  throw new Error("文件位置已不可用");
}

export function SourcePanel({
  citation,
  onClose,
  onOpenNote,
}: {
  citation: Citation;
  onClose: () => void;
  onOpenNote?: (noteId: string) => void;
}) {
  const mentionedDates = [...new Set(citation.mentionedDates ?? [])].flatMap((value) => {
    const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
    return match ? [`${match[1]}年${Number(match[2])}月${Number(match[3])}日`] : [];
  });
  const [error, setError] = useState("");
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const trigger = document.activeElement as HTMLElement | null;
    close.current?.focus();
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("keydown", escape);
      if (trigger?.isConnected) trigger.focus({ preventScroll: true });
    };
  }, [onClose]);
  useEffect(() => setError(""), [citation]);
  const open = async (reveal: boolean) => {
    setError("");
    try {
      await openSourcePath(reveal, citation.sourcePath, citation.storedPath);
    } catch {
      setError("文件无法打开，请检查原件和拾微保存的副本。");
    }
  };
  return (
    <aside className="chat-source-panel" aria-label="来源详情">
      <header>
        <div className="min-w-0">
          <p className="text-xs font-medium text-indigo">
            [{citation.citationId}] {citation.sourceType === "user_note" ? "笔记出处" : "原始出处"}
          </p>
          <h2 className="mt-2 break-words font-semibold">
            {citation.sourceFilename}
          </h2>
        </div>
        <button
          ref={close}
          className="chat-icon-button"
          onClick={onClose}
          aria-label="关闭来源详情"
        >
          <X size={18} />
        </button>
      </header>
      <div className="chat-source-body">
        <div className="flex flex-wrap gap-2 text-xs text-muted">
          {citation.pageNumber && <span>第 {citation.pageNumber} 页</span>}
          {citation.sheetName && <span>工作表 {citation.sheetName}</span>}
          {citation.slideNumber && <span>幻灯片 {citation.slideNumber}</span>}
          {citation.headingPath && <span>{citation.headingPath}</span>}
        </div>
        <p className="mt-5 text-xs font-medium text-muted">对应原文</p>
        <blockquote className="mt-3 whitespace-pre-wrap break-words text-sm leading-7">
          {citation.snippet}
        </blockquote>
        {citation.sourceType === "user_note" ? (
          <div className="mt-6 border-t border-line pt-4 text-xs leading-6 text-muted">
            <p>类型：笔记</p>
            {mentionedDates.length > 0 && <p>记录中提及日期：{mentionedDates.join("、")}</p>}
            {citation.noteCreatedAt && (
              <p>创建于 {new Date(citation.noteCreatedAt).toLocaleString("zh-CN")}</p>
            )}
            {citation.noteUpdatedAt && (
              <p>更新于 {new Date(citation.noteUpdatedAt).toLocaleString("zh-CN")}</p>
            )}
          </div>
        ) : (
          <details className="mt-6 border-t border-line pt-4 text-xs text-muted">
            <summary>文件位置与导入时间</summary>
            <p className="mt-3 break-all leading-5">
              {citation.sourcePath ?? "原件位置已不可用"}
            </p>
            {citation.importedAt && (
              <p className="mt-2">
                {new Date(citation.importedAt).toLocaleString("zh-CN")}
              </p>
            )}
          </details>
        )}
      </div>
      <footer>
        {error && (
          <p role="alert" className="mb-3 text-xs text-red-700">
            {error}
          </p>
        )}
        {citation.sourceType === "user_note" ? (
          <Button
            onClick={() => {
              if (citation.noteId) onOpenNote?.(citation.noteId);
            }}
            disabled={!citation.noteId || !onOpenNote}
          >
            <NotebookPen size={14} />
            打开这条笔记
          </Button>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={() => void open(false)}
              disabled={!citation.sourcePath && !citation.storedPath}
            >
              <ExternalLink size={14} />
              打开文件
            </Button>
            <Button
              variant="secondary"
              onClick={() => void open(true)}
              disabled={!citation.sourcePath && !citation.storedPath}
            >
              <FolderSearch size={14} />
              定位文件
            </Button>
          </div>
        )}
        <p className="mt-3 text-[11px] text-muted">可继续提问 · Esc 关闭出处</p>
      </footer>
    </aside>
  );
}
