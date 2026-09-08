import { invoke } from "@tauri-apps/api/core";
import { indexNote, updateNote } from "./notes";

vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(), isTauri: () => true }));

it("saves local text separately from a version-scoped optional indexing request", async () => {
  const saved = { id: "n1", title: "会议", content: "38~50天", updatedAt: "revision-2" };
  vi.mocked(invoke).mockResolvedValue({ note: saved });
  expect(await updateNote("n1", "会议", "38~50天")).toEqual(saved);
  expect(invoke).toHaveBeenCalledExactlyOnceWith("update_note", { noteId: "n1", title: "会议", content: "38~50天" });
  expect(await indexNote("n1", saved.updatedAt)).toEqual(saved);
  expect(invoke).toHaveBeenLastCalledWith("index_note", { noteId: "n1", revision: "revision-2" });
});

it("accepts a skipped stale or deleted index request without inventing a saved note", async () => {
  vi.mocked(invoke).mockResolvedValue({ skipped: "deleted" });
  expect(await indexNote("n1", "old-version")).toBeUndefined();
});
