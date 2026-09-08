import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { beginLibraryActivity } from "./library-location";

export type Citation = {
  citationId: string;
  documentId: string;
  chunkId: string;
  sourceFilename: string;
  sourcePath?: string;
  storedPath?: string;
  importedAt?: string;
  pageNumber?: number;
  sheetName?: string;
  slideNumber?: number;
  headingPath?: string;
  snippet: string;
  sourceType?: "imported_file" | "user_note";
  noteId?: string;
  noteCreatedAt?: string;
  noteUpdatedAt?: string;
  mentionedDates?: string[];
};

export type ChatAnswer = {
  answer: string;
  citations: Citation[];
  mode?: "ai" | "local_search";
  answerKind?: AnswerKind;
  sourceMatches?: SourceMatch[];
  notice?: string;
  conversationId: string;
  retrievalDebug?: unknown;
};

export type AnswerKind =
  | "general"
  | "knowledge"
  | "source_lookup"
  | "library_overview"
  | "not_found"
  | "clarification";
export type SourceMatch = {
  sourceId: string;
  filename: string;
  originalPath: string;
  storedPath: string;
  importedAt: string;
  status: string;
  sourceType?: "imported_file" | "user_note";
  noteId?: string;
  noteCreatedAt?: string;
  noteUpdatedAt?: string;
};

export type ConversationSummary = {
  id: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  preview?: string;
};

export type ConversationMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  createdAt: string;
  error?: string;
  citations: Citation[];
  answerKind?: AnswerKind;
  sourceMatches?: SourceMatch[];
  notice?: string;
};

export type ConversationDetail = ConversationSummary & {
  messages: ConversationMessage[];
};

export async function askKnowledge(
  query: string,
  conversationId?: string,
  onToken?: (token: string) => void,
  requestId: string = crypto.randomUUID(),
): Promise<ChatAnswer> {
  const done = beginLibraryActivity("正在生成回答");
  let unlisten: (() => void) | undefined;
  try {
    if (!isTauri()) {
      throw new Error("完整问答需要在拾微桌面应用中使用");
    }
    unlisten = onToken
    ? await listen<{ token: string; requestId: string }>(
        "chat-token",
        (event) => {
          if (event.payload.requestId === requestId)
            onToken(event.payload.token);
        },
      )
    : undefined;
    return await invoke<ChatAnswer>("chat_ask", {
      query,
      conversationId,
      requestId,
    });
  } finally {
    try { unlisten?.(); } finally { done(); }
  }
}

export async function listConversations(
  query = "",
  offset = 0,
): Promise<ConversationSummary[]> {
  if (!isTauri()) return [];
  const result = await invoke<{ conversations: ConversationSummary[] }>(
    "list_conversations",
    { query, offset },
  );
  return result.conversations;
}

export async function getConversation(
  conversationId: string,
): Promise<ConversationDetail> {
  const result = await invoke<{ conversation: ConversationDetail }>(
    "get_conversation",
    { conversationId },
  );
  return result.conversation;
}

export async function deleteConversation(conversationId: string): Promise<void> {
  await invoke("delete_conversation", { conversationId });
}

export async function cancelChat(requestId: string): Promise<void> {
  await invoke("chat_cancel", { requestId });
}

export async function openOriginal(path: string): Promise<void> {
  await invoke("open_original", { path });
}

export async function revealOriginal(path: string): Promise<void> {
  await invoke("reveal_original", { path });
}
