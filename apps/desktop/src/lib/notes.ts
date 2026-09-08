import { invoke, isTauri } from "@tauri-apps/api/core";

export type Note = {
  id: string;
  sourceId: string;
  title: string;
  content: string;
  displayTitle: string;
  createdAt: string;
  updatedAt: string;
  retrieval?: {
    revision: string;
    keyword: "ready" | "pending" | "empty";
    semantic?: "disabled" | "pending" | "ready" | "failed" | "requires_rebuild" | "empty";
  };
};

export async function listNotes(query = ""): Promise<Note[]> {
  if (!isTauri()) return [];
  const result = await invoke<{ notes: Note[] }>("list_notes", { query });
  return result.notes;
}

export async function getNote(noteId: string): Promise<Note> {
  const result = await invoke<{ note: Note }>("get_note", { noteId });
  return result.note;
}

export async function createNote(): Promise<Note> {
  const result = await invoke<{ note: Note }>("create_note");
  return result.note;
}

export async function updateNote(
  noteId: string,
  title: string,
  content: string,
): Promise<Note> {
  const result = await invoke<{ note: Note }>("update_note", {
    noteId,
    title,
    content,
  });
  return result.note;
}

export async function deleteNote(noteId: string): Promise<void> {
  await invoke("delete_note", { noteId });
}

export async function indexNote(noteId: string, revision: string): Promise<Note | undefined> {
  const result = await invoke<{ note?: Note }>("index_note", { noteId, revision });
  return result.note;
}
