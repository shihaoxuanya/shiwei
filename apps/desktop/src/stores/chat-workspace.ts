import { createStore } from "zustand/vanilla";
import {
  askKnowledge,
  deleteConversation,
  getConversation,
  listConversations,
  type ConversationMessage,
  type ConversationSummary,
} from "../lib/chat";

export type ChatSession = {
  key: string;
  conversationId?: string;
  title: string;
  draft: string;
  messages: ConversationMessage[];
  streaming: string;
  status: "idle" | "loading" | "searching" | "error";
  error?: string;
  failed?: { id: string; query: string };
  loaded: boolean;
  unread: boolean;
  scrollTop?: number;
};

type Workspace = {
  selected: string;
  sessions: Record<string, ChatSession>;
  conversations: ConversationSummary[];
  historyError?: string;
  historyLoading: boolean;
  historyQuery: string;
  hasMore: boolean;
  pending?: { key: string; requestId: string; startedAt: number };
  patch: (key: string, changes: Partial<ChatSession>) => void;
  setDraft: (draft: string) => void;
  newConversation: () => void;
  select: (id: string) => Promise<void>;
  refresh: (query?: string, more?: boolean) => Promise<void>;
  send: (text?: string, retry?: { id: string; query: string }) => Promise<void>;
  removeConversation: (id: string) => Promise<void>;
};

function blank(): ChatSession {
  return {
    key: crypto.randomUUID(),
    title: "新对话",
    draft: "",
    messages: [],
    streaming: "",
    status: "idle",
    loaded: true,
    unread: false,
  };
}

// Memory-only workspace. SQLite remains the worker's responsibility. Keeping a
// session separate from the selected view prevents late streams crossing chats.
export function createChatWorkspace() {
  const first = blank();
  let historyEpoch = 0;
  return createStore<Workspace>((set, get) => ({
    selected: first.key,
    sessions: { [first.key]: first },
    conversations: [],
    historyLoading: false,
    historyQuery: "",
    hasMore: false,
    patch: (key, changes) =>
      set((s) => ({
        sessions: { ...s.sessions, [key]: { ...s.sessions[key], ...changes } },
      })),
    setDraft: (draft) => get().patch(get().selected, { draft }),
    newConversation: () => {
      const unused = Object.values(get().sessions).find(
        (s) => !s.conversationId && !s.messages.length && !s.draft,
      );
      const next = unused ?? blank();
      set((s) => ({
        selected: next.key,
        sessions: { ...s.sessions, [next.key]: next },
      }));
    },
    select: async (id) => {
      const known = Object.values(get().sessions).find(
        (s) => s.key === id || s.conversationId === id,
      );
      const key = known?.key ?? id;
      if (known?.loaded) {
        set({ selected: key });
        get().patch(key, { unread: false });
        return;
      }
      const summary = get().conversations.find((c) => c.id === id);
      set((s) => ({
        selected: key,
        sessions: {
          ...s.sessions,
          [key]: {
            ...(known ?? blank()),
            key,
            conversationId: id,
            title: summary?.title ?? "对话",
            loaded: false,
            status: "loading",
            error: undefined,
          },
        },
      }));
      try {
        const conversation = await getConversation(id);
        get().patch(key, {
          messages: conversation.messages,
          title: conversation.title,
          status: "idle",
          loaded: true,
        });
      } catch (reason) {
        get().patch(key, {
          status: "error",
          error: String(reason),
          loaded: false,
        });
      }
    },
    refresh: async (query = get().historyQuery, more = false) => {
      const epoch = ++historyEpoch;
      const offset = more ? get().conversations.length : 0;
      set({
        historyLoading: true,
        historyQuery: query,
        historyError: undefined,
      });
      try {
        const list = await listConversations(query, offset);
        if (epoch === historyEpoch)
          set((s) => ({
            conversations: more
              ? [
                  ...s.conversations,
                  ...list.filter(
                    (c) => !s.conversations.some((old) => old.id === c.id),
                  ),
                ]
              : list,
            hasMore: list.length === 50,
          }));
      } catch {
        if (epoch === historyEpoch)
          set({ historyError: "历史对话暂时无法读取，可以重试。" });
      } finally {
        if (epoch === historyEpoch) set({ historyLoading: false });
      }
    },
    send: async (text, retry) => {
      const key = get().selected;
      const session = get().sessions[key];
      const query = (retry?.query ?? text ?? session.draft).trim();
      if (!query || query.length > 2000 || get().pending || !session.loaded)
        return;
      const requestId = crypto.randomUUID();
      const userId = `user-${requestId}`;
      // Synchronous lock applies even to repeated Enter before React re-renders.
      set({ pending: { key, requestId, startedAt: Date.now() } });
      get().patch(key, {
        status: "searching",
        error: undefined,
        failed: undefined,
        draft: text !== undefined || retry ? session.draft : "",
        streaming: "",
        title: session.conversationId ? session.title : query.slice(0, 60),
        messages: [
          ...session.messages.filter((m) => m.id !== retry?.id),
          {
            id: userId,
            role: "user",
            content: query,
            createdAt: new Date().toISOString(),
            citations: [],
          },
        ],
      });
      try {
        const answer = await askKnowledge(
          query,
          session.conversationId,
          (token) => {
            if (get().pending?.requestId === requestId)
              get().patch(key, {
                streaming: get().sessions[key].streaming + token,
              });
          },
          requestId,
        );
        get().patch(key, {
          conversationId: answer.conversationId,
          status: "idle",
          streaming: "",
          unread: get().selected !== key,
          messages: [
            ...get().sessions[key].messages,
            {
              id: `assistant-${requestId}`,
              role: "assistant",
              content: answer.answer,
              createdAt: new Date().toISOString(),
              citations: answer.citations,
              answerKind: answer.answerKind,
              sourceMatches: answer.sourceMatches,
              notice: answer.notice,
            },
          ],
        });
        void get().refresh();
      } catch (reason) {
        get().patch(key, {
          status: "error",
          streaming: "",
          error: reason instanceof Error ? reason.message : String(reason),
          failed: { id: userId, query },
          unread: get().selected !== key,
        });
      } finally {
        if (get().pending?.requestId === requestId) set({ pending: undefined });
      }
    },
    removeConversation: async (id) => {
      const target = Object.values(get().sessions).find(
        (session) => session.conversationId === id,
      );
      if (target && get().pending?.key === target.key) {
        throw new Error("这段对话正在回答，请稍后再删除");
      }
      await deleteConversation(id);
      const isCurrent = target?.key === get().selected;
      const replacement = isCurrent ? blank() : undefined;
      set((state) => {
        const sessions = { ...state.sessions };
        if (target) delete sessions[target.key];
        if (replacement) sessions[replacement.key] = replacement;
        return {
          conversations: state.conversations.filter((item) => item.id !== id),
          sessions,
          selected: replacement?.key ?? state.selected,
        };
      });
      void get().refresh();
    },
  }));
}
