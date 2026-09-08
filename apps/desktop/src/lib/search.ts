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
  sourceId?: string;
  sourceType?: string;
  pageNumber?: number;
  sheetName?: string;
  slideNumber?: number;
};

export async function searchLocal(query: string, limit = 20, sourceType?: "imported_file"): Promise<LexicalHit[]> {
  if (!isTauri()) return [];
  return invoke<LexicalHit[]>("search_lexical", sourceType ? { query, limit, sourceType } : { query, limit });
}
