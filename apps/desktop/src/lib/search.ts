import { invoke, isTauri } from "@tauri-apps/api/core";

export type LexicalHit = {
  chunkId: string;
  documentId: string;
  content: string;
  documentTitle: string;
  filename: string;
  headingPath?: string;
  lexicalScore: number;
  matchedBy: string[];
};

export async function searchLocal(query: string, limit = 20): Promise<LexicalHit[]> {
  if (!isTauri()) return [];
  return invoke<LexicalHit[]>("search_lexical", { query, limit });
}
