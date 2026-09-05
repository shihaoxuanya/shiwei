import { invoke, isTauri } from "@tauri-apps/api/core";

export type AnalyticsEvent = "import_started" | "import_completed" | "import_failed" | "note_created" | "question_asked" | "retrieval_succeeded" | "retrieval_abstained" | "citation_clicked" | "app_error";
export type SafeMetadata = { file_count_bucket?: "1" | "2-10" | "11-100" | "100+"; success_count?: number; failure_count?: number; duration_ms?: number; conversation_mode?: "knowledge_first"; error_type?: "frontend_error" | "unhandled_rejection" | "import_error" | "chat_error"; frames?: Array<{ module: "frontend"; line?: number; column?: number }> };
export interface AnalyticsProvider { track(event: AnalyticsEvent, properties: SafeMetadata): Promise<void> }
class NativeAnalyticsProvider implements AnalyticsProvider {
  async track(event: AnalyticsEvent, properties: SafeMetadata) { await invoke("analytics_track", { event, properties }); }
}
export class AnalyticsService {
  private enabled = false;
  constructor(private provider: AnalyticsProvider) {}
  setEnabled(enabled: boolean) { this.enabled = enabled; }
  track(event: AnalyticsEvent, properties: SafeMetadata = {}) {
    if (!this.enabled) return;
    // The native boundary independently validates consent and strips every non-allowlisted field.
    try { void this.provider.track(event, properties).catch(() => {}); } catch { /* best effort */ }
  }
}
export const analytics = new AnalyticsService(new NativeAnalyticsProvider());
export function fileCountBucket(n: number): SafeMetadata["file_count_bucket"] { return n <= 1 ? "1" : n <= 10 ? "2-10" : n <= 100 ? "11-100" : "100+"; }

export function safeFrontendFrames(stack: unknown): NonNullable<SafeMetadata["frames"]> {
  if (typeof stack !== "string") return [];
  // Discard the exception message and all user paths/URLs/function names. Keep only positions
  // within our bundled application JS (no code context or request breadcrumbs).
  return stack.split("\n").slice(1, 30).flatMap((line) => {
    const match = /\/assets\/index-[A-Za-z0-9_-]+\.js:(\d+):(\d+)\)?$/.exec(line);
    return match ? [{ module: "frontend" as const, line: Number(match[1]), column: Number(match[2]) }] : [];
  }).slice(0, 12);
}
export function installErrorReporting() {
  if (!isTauri()) return () => {};
  const error = (event: ErrorEvent) => analytics.track("app_error", { error_type: "frontend_error", frames: safeFrontendFrames(event.error?.stack) });
  const rejection = (event: PromiseRejectionEvent) => analytics.track("app_error", { error_type: "unhandled_rejection", frames: safeFrontendFrames(event.reason?.stack) });
  window.addEventListener("error", error);
  window.addEventListener("unhandledrejection", rejection);
  return () => { window.removeEventListener("error", error); window.removeEventListener("unhandledrejection", rejection); };
}
