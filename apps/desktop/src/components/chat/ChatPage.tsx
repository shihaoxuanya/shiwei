import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useStore } from "zustand";
import { isTauri } from "@tauri-apps/api/core";
import {
  ArrowDown,
  ArrowUp,
  FileSearch,
  History,
  MessageSquareText,
  MoreHorizontal,
  PanelLeftClose,
  Plus,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { type AnswerKind, type Citation } from "../../lib/chat";
import { analytics } from "../../lib/analytics";
import { createChatWorkspace } from "../../stores/chat-workspace";
import { Button } from "../ui/button";
import { CopyButton, MessageContent } from "./MessageContent";
import { CitationChip, SourceMatchCard, SourcePanel } from "./Sources";
import "./chat.css";

const answerLabels: Record<AnswerKind, string> = {
  general: "日常对话",
  knowledge: "依据资料回答",
  source_lookup: "找到的文件",
  library_overview: "资料库概览",
  not_found: "未找到资料",
  clarification: "需要你确认",
};

function shortDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

function previewText(value: string) {
  return value
    .replace(/(^|\n)\s{0,3}#{1,6}\s+/g, "$1")
    .replace(/[*`]|\[[SN]\d+\]/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

export function ChatPage({
  initialQuery = "",
  requestKey = "initial",
  active = true,
  onOpenSettings,
  onOpenNote,
}: {
  initialQuery?: string;
  requestKey?: string;
  active?: boolean;
  onOpenSettings: () => void;
  onOpenNote?: (noteId: string) => void;
}) {
  const [store] = useState(createChatWorkspace);
  const state = useStore(store);
  const session = state.sessions[state.selected];
  const [historyOpen, setHistoryOpen] = useState(true);
  const [historySearch, setHistorySearch] = useState("");
  const [citation, setCitation] = useState<Citation | null>(null);
  const [awayFromBottom, setAwayFromBottom] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [historyMenu, setHistoryMenu] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<{ id: string; title: string } | null>(null);
  const [deleteError, setDeleteError] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [toast, setToast] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const displayedKey = useRef("");
  const homeRequests = useRef(new Set<string>());
  const closeSource = useCallback(() => setCitation(null), []);
  const selectCitation = useCallback((value: Citation) => { setCitation(value); analytics.track("citation_clicked"); }, []);
  const busyHere = state.pending?.key === state.selected;
  const busyElsewhere = state.pending && !busyHere;
  const canSend =
    !!session.draft.trim() &&
    session.draft.length <= 2000 &&
    !state.pending &&
    session.loaded;

  useEffect(() => {
    const timer = setTimeout(
      () => void store.getState().refresh(historySearch),
      historySearch ? 250 : 0,
    );
    return () => clearTimeout(timer);
  }, [historySearch, store]);
  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (
        cancelled ||
        !initialQuery ||
        homeRequests.current.has(requestKey) ||
        !isTauri()
      )
        return;
      homeRequests.current.add(requestKey);
      const current = store.getState();
      if (
        current.sessions[current.selected].messages.length ||
        current.sessions[current.selected].draft
      )
        current.newConversation();
      if (current.pending) store.getState().setDraft(initialQuery);
      else void store.getState().send(initialQuery);
    });
    return () => {
      cancelled = true;
    };
  }, [initialQuery, requestKey, store]);

  useEffect(() => {
    setCitation(null);
    if (active) input.current?.focus({ preventScroll: true });
  }, [state.selected, active]);
  useEffect(() => {
    setElapsed(0);
    if (!busyHere) return;
    const start = state.pending?.startedAt ?? Date.now();
    setElapsed(Math.floor((Date.now() - start) / 1000));
    const timer = setInterval(
      () => setElapsed(Math.floor((Date.now() - start) / 1000)),
      1000,
    );
    return () => clearInterval(timer);
  }, [busyHere, state.pending?.startedAt]);
  useLayoutEffect(() => {
    if (!active || !input.current) return;
    input.current.style.height = "0px";
    input.current.style.height = `${Math.min(156, Math.max(48, input.current.scrollHeight))}px`;
  }, [session.draft, active]);
  useLayoutEffect(() => {
    const area = scroller.current;
    if (!active || !area) return;
    if (displayedKey.current !== state.selected) {
      displayedKey.current = state.selected;
      area.scrollTop = session.scrollTop ?? area.scrollHeight;
      stickToBottom.current =
        area.scrollHeight - area.scrollTop - area.clientHeight < 100;
    } else if (stickToBottom.current) area.scrollTop = area.scrollHeight;
    setAwayFromBottom(
      area.scrollHeight - area.scrollTop - area.clientHeight > 100,
    );
  }, [
    state.selected,
    session.messages,
    session.streaming,
    session.status,
    active,
  ]);

  const latest = () => {
    stickToBottom.current = true;
    if (scroller.current)
      scroller.current.scrollTop = scroller.current.scrollHeight;
    setAwayFromBottom(false);
  };
  const send = (text?: string) => {
    stickToBottom.current = true;
    void store.getState().send(text);
    input.current?.focus({ preventScroll: true });
  };
  const startNew = () => {
    state.newConversation();
    setCitation(null);
  };
  const choose = (id: string) => {
    setHistoryMenu(null);
    setCitation(null);
    void state.select(id);
  };
  const confirmDelete = async () => {
    if (!deleteTarget || deleting) return;
    setDeleting(true);
    setDeleteError("");
    try {
      await store.getState().removeConversation(deleteTarget.id);
      setDeleteTarget(null);
      setHistoryMenu(null);
      setCitation(null);
      setToast("对话已删除");
      window.setTimeout(() => setToast(""), 2200);
    } catch (reason) {
      setDeleteError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setDeleting(false);
    }
  };
  const localSessions = Object.values(state.sessions).filter(
    (s) => !s.conversationId && (s.messages.length > 0 || !!s.draft.trim()),
  );
  const search = historySearch.trim().toLocaleLowerCase();

  return (
    <section
      className={`chat-workspace ${historyOpen ? "history-open" : ""} ${citation ? "source-open" : ""}`}
      aria-label="对话工作区"
    >
      <div className="chat-workspace-inner">
        {historyOpen && (
          <aside className="chat-history" aria-label="历史对话">
            <div className="chat-history-heading">
              <h2>历史对话</h2>
              <button
                className="chat-icon-button"
                aria-label="收起历史对话"
                onClick={() => setHistoryOpen(false)}
              >
                <PanelLeftClose size={17} />
              </button>
            </div>
            <Button
              className="chat-new-button"
              variant="secondary"
              onClick={startNew}
            >
              <Plus size={16} />
              新建对话
            </Button>
            <label className="chat-history-search">
              <Search size={15} />
              <input
                aria-label="搜索历史对话"
                placeholder="搜索标题或聊天内容"
                maxLength={200}
                value={historySearch}
                onChange={(event) => setHistorySearch(event.target.value)}
              />
              {historySearch && (
                <button
                  className="chat-icon-button"
                  aria-label="清空历史搜索"
                  onClick={() => setHistorySearch("")}
                >
                  <X size={13} />
                </button>
              )}
            </label>
            <div className="chat-history-list">
              {localSessions
                .filter(
                  (s) =>
                    !search ||
                    `${s.title} ${s.draft}`
                      .toLocaleLowerCase()
                      .includes(search),
                )
                .map((s) => (
                  <button
                    key={s.key}
                    className={`chat-history-item ${state.selected === s.key ? "selected" : ""}`}
                    onClick={() => choose(s.key)}
                    aria-current={state.selected === s.key ? "true" : undefined}
                  >
                    <span className="chat-history-title">
                      {s.title === "新对话" ? s.draft : s.title}
                    </span>
                    <span className="chat-history-meta">
                      {state.pending?.key === s.key
                        ? "正在回答…"
                        : s.status === "error"
                          ? "发送失败"
                          : "草稿"}
                    </span>
                  </button>
                ))}
              {state.conversations.map((conversation) => {
                const cached = Object.values(state.sessions).find(
                  (s) => s.conversationId === conversation.id,
                );
                return (
                  <div
                    key={conversation.id}
                    className={`chat-history-row ${session.conversationId === conversation.id ? "selected" : ""}`}
                  >
                    <button
                      className="chat-history-item"
                      onClick={() => choose(conversation.id)}
                      aria-current={
                        session.conversationId === conversation.id
                          ? "true"
                          : undefined
                      }
                    >
                      <span
                        className="chat-history-title"
                        title={conversation.title}
                      >
                        {conversation.title}
                      </span>
                      {conversation.preview && (
                        <span className="chat-history-preview">
                          {previewText(conversation.preview)}
                        </span>
                      )}
                      <span className="chat-history-meta">
                        {state.pending?.key === cached?.key && state.pending
                          ? "正在回答…"
                          : cached?.unread
                            ? "新回复"
                            : cached?.draft
                              ? "有草稿"
                              : shortDate(conversation.updatedAt)}
                      </span>
                    </button>
                    <button
                      className="chat-history-menu-trigger"
                      aria-label={`对话操作：${conversation.title}`}
                      aria-expanded={historyMenu === conversation.id}
                      onClick={(event) => {
                        event.stopPropagation();
                        setHistoryMenu((current) =>
                          current === conversation.id ? null : conversation.id,
                        );
                      }}
                    >
                      <MoreHorizontal size={16} />
                    </button>
                    {historyMenu === conversation.id && (
                      <div className="chat-history-menu" role="menu">
                        <button
                          role="menuitem"
                          onClick={() => {
                            setDeleteError("");
                            setDeleteTarget({ id: conversation.id, title: conversation.title });
                            setHistoryMenu(null);
                          }}
                        >
                          <Trash2 size={14} />
                          删除对话
                        </button>
                      </div>
                    )}
                  </div>
                );
              })}
              {!state.historyLoading &&
                !state.conversations.length &&
                !localSessions.length && (
                  <p className="chat-history-empty">
                    {search
                      ? "没有匹配的对话，试试关键词。"
                      : "聊过的内容会保存在这里。"}
                  </p>
                )}
              {state.historyLoading && (
                <p className="chat-history-empty" role="status">
                  正在读取历史…
                </p>
              )}
              {state.historyError && (
                <div className="chat-history-empty" role="alert">
                  {state.historyError}
                  <button
                    className="chat-text-action mt-2"
                    onClick={() => void state.refresh()}
                  >
                    重新加载
                  </button>
                </div>
              )}
              {state.hasMore && !state.historyLoading && (
                <button
                  className="chat-more"
                  onClick={() => void state.refresh(undefined, true)}
                >
                  加载更多对话
                </button>
              )}
            </div>
            <p className="chat-history-footnote">对话保存在这台电脑上</p>
          </aside>
        )}
        <div className="chat-main">
          <header className="chat-header">
            <div className="flex min-w-0 items-center gap-3">
              <button
                className="chat-icon-button"
                aria-label={
                  historyOpen && !citation ? "隐藏历史对话" : "显示历史对话"
                }
                aria-expanded={historyOpen && !citation}
                onClick={() => {
                  setCitation(null);
                  setHistoryOpen(citation ? true : !historyOpen);
                }}
              >
                <History size={19} />
              </button>
              <div className="min-w-0">
                <h1 title={session.title}>
                  {session.messages.length || session.conversationId
                    ? session.title
                    : "对话"}
                </h1>
                <p>知识库优先 · 也可以聊聊日常</p>
              </div>
            </div>
            {!historyOpen && (
              <Button variant="ghost" onClick={startNew}>
                <Plus size={16} />
                新建对话
              </Button>
            )}
          </header>
          <div
            className="chat-scroll"
            ref={scroller}
            tabIndex={0}
            aria-label="对话消息"
            onScroll={(event) => {
              const area = event.currentTarget;
              stickToBottom.current =
                area.scrollHeight - area.scrollTop - area.clientHeight < 100;
              setAwayFromBottom(!stickToBottom.current);
              state.patch(state.selected, { scrollTop: area.scrollTop });
            }}
          >
            <div className="chat-messages">
              {!session.messages.length &&
                session.status !== "loading" &&
                session.loaded && (
                  <div className="chat-welcome">
                    <div className="chat-welcome-icon">
                      <Sparkles size={24} />
                    </div>
                    <h2>想起什么，都可以问问拾微</h2>
                    <p>
                      找回一份文件，接着上次的思路，或聊一个新问题。
                      <br />
                      涉及你的经历时，我会先找资料，再给答案。
                    </p>
                    <div className="chat-starters">
                      <button
                        onClick={() => send("我的资料库里有什么资料？")}
                        disabled={!!state.pending}
                      >
                        <FileSearch size={18} />
                        <span>
                          看看我的资料库<small>从已有资料开始</small>
                        </span>
                        <ArrowUp size={14} />
                      </button>
                      <button
                        onClick={() => {
                          state.setDraft("我想了解 ");
                          input.current?.focus();
                        }}
                      >
                        <MessageSquareText size={18} />
                        <span>
                          聊一个新问题<small>想法、知识、日常</small>
                        </span>
                        <ArrowUp size={14} />
                      </button>
                    </div>
                  </div>
                )}
              {session.status === "loading" && (
                <p role="status" className="chat-loading">
                  正在打开这段对话…
                </p>
              )}
              {session.messages.map((message) =>
                message.role === "user" ? (
                  <div
                    key={message.id}
                    className="chat-user"
                    data-message-role="user"
                  >
                    {message.content}
                  </div>
                ) : (
                  <article
                    key={message.id}
                    className="chat-answer"
                    data-message-role="assistant"
                  >
                    <div className="chat-answer-label">
                      <Sparkles size={15} />
                      {message.answerKind
                        ? answerLabels[message.answerKind]
                        : "拾微的回答"}
                    </div>
                    <MessageContent
                      text={message.content}
                      citations={message.citations}
                      onCitation={selectCitation}
                    />
                    {message.notice === "provider_not_configured" && (
                      <Button
                        className="mt-3"
                        variant="secondary"
                        onClick={onOpenSettings}
                      >
                        配置对话模型
                      </Button>
                    )}
                    {message.sourceMatches?.length &&
                    !(
                      message.answerKind === "knowledge" &&
                      message.citations.length
                    ) ? (
                      <div className="chat-files">
                        {message.sourceMatches.slice(0, 3).map((source) => (
                          <SourceMatchCard
                            key={source.sourceId}
                            source={source}
                            disabled={!!state.pending}
                            onChoose={() => send(`请总结 ${source.filename}`)}
                            onOpenNote={onOpenNote}
                          />
                        ))}
                        {message.sourceMatches.length > 3 && (
                          <details>
                            <summary className="chat-more">
                              另外 {message.sourceMatches.length - 3} 份资料
                            </summary>
                            {message.sourceMatches.slice(3).map((source) => (
                              <SourceMatchCard
                                key={source.sourceId}
                                source={source}
                                disabled={!!state.pending}
                                onChoose={() =>
                                  send(`请总结 ${source.filename}`)
                                }
                                onOpenNote={onOpenNote}
                              />
                            ))}
                          </details>
                        )}
                      </div>
                    ) : null}
                    {message.citations.length > 0 && (
                      <div className="chat-citations">
                        <span>原始出处</span>
                        {message.citations.map((item) => (
                          <CitationChip
                            key={item.citationId}
                            citation={item}
                            onSelect={selectCitation}
                          />
                        ))}
                      </div>
                    )}
                    <div className="chat-answer-actions">
                      <CopyButton text={message.content} />
                    </div>
                  </article>
                ),
              )}
              {busyHere && (
                <article
                  className="chat-answer chat-streaming"
                  aria-busy="true"
                >
                  <div className="chat-answer-label">
                    <span className="chat-status-dot" />
                    {session.streaming ? "正在回答" : "正在查找资料、组织回答"}
                    <span className="ml-auto font-normal tabular-nums text-muted">
                      {elapsed} 秒
                    </span>
                  </div>
                  {session.streaming ? (
                    <MessageContent text={session.streaming} />
                  ) : (
                    <p className="chat-wait-note">
                      {elapsed >= 15
                        ? "模型回复需要一些时间。你可以先查看历史，回答会留在这段对话里。"
                        : "可以先写下一条问题，回复会显示在这里。"}
                    </p>
                  )}
                </article>
              )}
              {session.error && (
                <div className="chat-error" role="alert">
                  <p>{session.error}</p>
                  <div className="mt-3 flex flex-wrap gap-2">
                    {session.failed && (
                      <Button
                        variant="secondary"
                        disabled={!!state.pending}
                        onClick={() => {
                          stickToBottom.current = true;
                          void state.send(undefined, session.failed);
                        }}
                      >
                        重试发送
                      </Button>
                    )}
                    {!session.loaded && (
                      <Button
                        variant="secondary"
                        onClick={() => void state.select(session.key)}
                      >
                        重新打开对话
                      </Button>
                    )}
                    {session.error.includes("模型") && (
                      <Button variant="secondary" onClick={onOpenSettings}>
                        前往设置
                      </Button>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
          <div className="chat-compose-area">
            {awayFromBottom && (
              <button className="chat-latest" onClick={latest}>
                <ArrowDown size={15} />
                回到最新消息
              </button>
            )}
            {busyElsewhere && (
              <div className="chat-background-status" role="status">
                另一段对话正在回答，完成后即可发送。
                <button onClick={() => choose(state.pending!.key)}>
                  查看进度
                </button>
              </div>
            )}
            <div className="chat-composer">
              <textarea
                ref={input}
                aria-label="消息"
                placeholder="找一份资料、问问过去的记录，或聊一个问题"
                value={session.draft}
                rows={2}
                onChange={(event) => state.setDraft(event.target.value)}
                onKeyDown={(event) => {
                  if (
                    event.key === "Enter" &&
                    !event.shiftKey &&
                    !event.nativeEvent.isComposing &&
                    event.keyCode !== 229
                  ) {
                    event.preventDefault();
                    if (canSend) send();
                  }
                }}
              />
              <div className="chat-composer-bottom">
                <span
                  className={
                    session.draft.length > 2000 ? "text-red-700" : "text-muted"
                  }
                >
                  {session.draft.length > 1800
                    ? `${session.draft.length} / 2000 字`
                    : "Enter 发送 · Shift + Enter 换行"}
                </span>
                <Button
                  className="chat-send"
                  aria-label="发送"
                  title={
                    session.draft.length > 2000
                      ? "问题不能超过 2000 字"
                      : state.pending
                        ? "等待当前回答完成；可以先写下一条"
                        : "发送消息"
                  }
                  disabled={!canSend}
                  onClick={() => send()}
                >
                  <ArrowUp size={17} />
                  发送
                </Button>
              </div>
            </div>
            <p className="chat-privacy">
              {session.draft.length > 2000
                ? "内容较长，请分成几条提问；已输入的文字不会丢失。"
                : "个人记录以原始出处为准。只有回答所需的片段会发送给你配置的模型。"}
            </p>
            <span className="sr-only" role="status">
              {busyHere
                ? "拾微正在回答"
                : session.error
                  ? "本次请求失败"
                  : session.messages.length
                    ? "回答已完成"
                    : "可以开始提问"}
            </span>
          </div>
        </div>
        {citation && (
          <SourcePanel
            citation={citation}
            onClose={closeSource}
            onOpenNote={onOpenNote}
          />
        )}
      </div>
      {deleteTarget && (
        <div className="chat-dialog-backdrop" role="presentation">
          <div
            className="chat-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-conversation-title"
          >
            <h2 id="delete-conversation-title">删除这段对话？</h2>
            <p>删除后无法恢复，但不会删除你导入的资料或笔记。</p>
            {deleteError && <p className="chat-dialog-error" role="alert">{deleteError}</p>}
            <div className="chat-dialog-actions">
              <Button
                variant="secondary"
                disabled={deleting}
                onClick={() => setDeleteTarget(null)}
              >
                取消
              </Button>
              <button
                className="chat-danger-button"
                disabled={deleting}
                onClick={() => void confirmDelete()}
              >
                {deleting ? "正在删除…" : "删除"}
              </button>
            </div>
          </div>
        </div>
      )}
      {toast && <div className="chat-toast" role="status">{toast}</div>}
    </section>
  );
}
