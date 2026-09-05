import { AnalyticsService, safeFrontendFrames } from "./analytics";
import { FeatureFlagService } from "./feature-flags";
import { downloadPercent, initialUpdate, updateReducer, updateErrorCopy } from "./updates";

it("disabled analytics is a no-op and errors never escape into product operations", async () => {
  const track = vi.fn().mockRejectedValue(new Error("network down"));
  const service = new AnalyticsService({ track });
  service.track("question_asked"); expect(track).not.toHaveBeenCalled();
  service.setEnabled(true); expect(() => service.track("note_created")).not.toThrow();
  await Promise.resolve();
  service.setEnabled(false); service.track("citation_clicked"); expect(track).toHaveBeenCalledTimes(1);
  const throws = new AnalyticsService({ track: () => { throw new Error("provider failed"); } });
  throws.setEnabled(true); expect(() => throws.track("question_asked")).not.toThrow();
});
it("error stack cleaning retains only bundled code positions, never paths or question text", () => {
  const frames = safeFrontendFrames("Error: 我的秘密问题 API Key\n at a (C:/Users/private/note.txt:5:9)\n at b (http://tauri.localhost/assets/index-ABC123.js:42:7)\n at token (https://api.example.com/query?key=secret:1:1)");
  expect(frames).toEqual([{ module: "frontend", line: 42, column: 7 }]);
  expect(JSON.stringify(frames)).not.toMatch(/secret|private|Users|API|秘密/);
});
it("state reducer covers check, available, save, download, verification, installation", () => {
  let state = updateReducer(initialUpdate, { type: "check" });
  expect(state.phase).toBe("checking");
  state = updateReducer(state, { type: "result", result: { status: "available", version: "0.3.1", notes: "修复" } });
  state = updateReducer(state, { type: "save" });
  state = updateReducer(state, { type: "progress", phase: "downloading", received: 37, total: 100 });
  expect(downloadPercent(state)).toBe(37);
  state = updateReducer(state, { type: "progress", phase: "verifying" });
  expect(updateReducer(state, { type: "progress", phase: "downloading", received: 99 }).phase).toBe("verifying");
  expect(updateReducer(state, { type: "progress", phase: "installing" }).phase).toBe("installing");
});
it.each(["update_network", "update_manifest", "update_download", "update_signature", "update_install", "update_worker_busy", "update_save"])("%s is recoverable and never becomes a successful install", (code) => {
  const state = updateReducer(initialUpdate, { type: "failure", code });
  expect(state.phase).toBe("error"); expect(updateErrorCopy[code]).toBeTruthy();
  expect(updateReducer(state, { type: "progress", phase: "installing" })).toEqual(state);
});
it("unknown download sizes use indeterminate progress and oversized values are clamped", () => {
  expect(downloadPercent(initialUpdate)).toBeNull();
  expect(downloadPercent({ phase: "downloading", received: 120, total: 100 })).toBe(100);
});
it("mandatory policy survives progress but does not persist into a new check", () => {
  let state = updateReducer(initialUpdate, { type: "result", result: { status: "available", version: "0.3.1", mandatory: true } });
  expect(state.mandatory).toBe(true);
  state = updateReducer(state, { type: "save" });
  expect(state.mandatory).toBe(true);
  expect(updateReducer(state, { type: "failure", code: "update_withdrawn" }).phase).toBe("error");
  expect(updateReducer(state, { type: "check" }).mandatory).toBeUndefined();
});
it("future flags default locally even if a replacement provider fails", () => {
  expect(new FeatureFlagService().isEnabled("new_memory_consolidation")).toBe(false);
  expect(new FeatureFlagService({ isEnabled: () => { throw new Error("offline"); } }).isEnabled("x", true)).toBe(true);
});
