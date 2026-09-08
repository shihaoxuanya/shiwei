import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { create } from "zustand";
import { APP_VERSION } from "../release-version";
import { analytics } from "./analytics";

export type UpdatePhase = "idle" | "unconfigured" | "checking" | "current" | "available" | "saving" | "downloading" | "verifying" | "installing" | "error";
export type UpdateState = { phase: UpdatePhase; version?: string; notes?: string; mandatory?: boolean; received: number; total?: number; error?: string };
type UpdateAction = { type: "check" } | { type: "result"; result: { status: "available" | "current" | "unconfigured"; version?: string; notes?: string; mandatory?: boolean } } | { type: "progress"; phase: "downloading" | "verifying" | "installing"; received?: number; total?: number } | { type: "save" } | { type: "failure"; code: string };
export const initialUpdate: UpdateState = { phase: "idle", received: 0 };
export function updateReducer(state: UpdateState, action: UpdateAction): UpdateState {
  switch (action.type) {
    case "check": return { phase: "checking", received: 0 };
    case "result": return { phase: action.result.status, version: action.result.version, notes: action.result.notes, mandatory: action.result.mandatory === true, received: 0 };
    case "save": return { ...state, phase: "saving", error: undefined, received: 0 };
    case "progress": {
      if (!["saving", "downloading", "verifying", "installing"].includes(state.phase)) return state;
      const rank = { saving: 0, downloading: 1, verifying: 2, installing: 3 };
      if (rank[action.phase] < rank[state.phase as keyof typeof rank]) return state;
      return { ...state, phase: action.phase, received: Math.max(state.received, action.received ?? 0), total: action.total ?? state.total };
    }
    case "failure": return { ...state, phase: "error", error: action.code };
  }
}
export function downloadPercent(state: UpdateState): number | null { return state.total && state.total > 0 ? Math.min(100, Math.max(0, Math.floor(state.received / state.total * 100))) : null; }
export const updateErrorCopy: Record<string, string> = {
  update_withdrawn: "这个更新已暂停、撤销或变更，请重新检查更新。",
  update_identity: "暂时无法保存更新分组标识，请检查本地资料目录是否可写。",
  update_network: "暂时无法连接更新服务，请检查网络后重试。",
  update_manifest: "更新信息暂时不可用或格式不正确，请稍后重试。",
  update_download: "更新下载失败或中断，请稍后重试。",
  update_signature: "更新签名验证失败，已拒绝安装。",
  update_install: "无法启动更新安装，请稍后重试。",
  update_worker_busy: "仍有资料处理或对话进行中，请完成后再更新。",
  update_save: "笔记尚未保存成功，已停止更新，请先保存笔记。",
};
export type AnalyticsChoice = "undecided" | "enabled" | "disabled";
export type ReleaseStatus = { version: string; channel: string; updaterConfigured: boolean; analytics: { enabled: boolean; configured: boolean; choice?: AnalyticsChoice; needsChoice?: boolean } };
export const useReleaseStore = create<{ status: ReleaseStatus; update: UpdateState; visible: boolean; privacyError: string; privacySaving: boolean; privacyLoaded: boolean; setVisible: (visible: boolean) => void }>((set) => ({
  status: { version: APP_VERSION, channel: "stable", updaterConfigured: false, analytics: { enabled: false, configured: false, choice: "undecided", needsChoice: false } },
  update: initialUpdate, visible: false, privacyError: "", privacySaving: false, privacyLoaded: false, setVisible: (visible) => set({ visible }),
}));
let checking = false;
let installing = false;
let privacyRevision = 0;
function dispatch(action: UpdateAction) { useReleaseStore.setState((s) => ({ update: updateReducer(s.update, action) })); }
export async function refreshReleaseStatus() {
  if (!isTauri()) return;
  const revision = privacyRevision;
  try {
    const status = await invoke<ReleaseStatus>("release_status");
    if (revision !== privacyRevision) return;
    if (!status?.analytics || typeof status.analytics.enabled !== "boolean") throw new Error("invalid analytics state");
    // The native layer owns default/migration policy. Never infer enabled before it replies.
    const safeStatus = { ...status, analytics: { ...status.analytics, enabled: status.analytics.enabled === true } };
    useReleaseStore.setState({ status: safeStatus, privacyLoaded: true }); analytics.setEnabled(safeStatus.analytics.enabled);
  }
  catch {
    if (revision !== privacyRevision) return;
    analytics.setEnabled(false);
    useReleaseStore.setState((s) => ({ privacyLoaded: false, status: { ...s.status, analytics: { ...s.status.analytics, enabled: false } } }));
  }
}
export async function setAnalyticsConsent(enabled: boolean) {
  if (!isTauri() || useReleaseStore.getState().privacySaving) return;
  privacyRevision += 1;
  analytics.setEnabled(false); useReleaseStore.setState({ privacyError: "", privacySaving: true });
  try { await invoke("analytics_consent", { enabled }); await refreshReleaseStatus(); }
  catch { useReleaseStore.setState((s) => ({ privacyError: "无法保存隐私设置，基础统计保持关闭。请重试。", status: { ...s.status, analytics: { ...s.status.analytics, enabled: false, choice: "undecided" } } })); }
  finally { useReleaseStore.setState({ privacySaving: false }); }
}
export async function checkForUpdate(manual = false) {
  if (!isTauri() || checking || installing) return;
  checking = true; dispatch({ type: "check" });
  try {
    const result = await invoke<{ status: "available" | "current" | "unconfigured"; version?: string; notes?: string; mandatory?: boolean }>("update_check");
    dispatch({ type: "result", result });
    if (result.status === "available") useReleaseStore.setState({ visible: true });
  } catch (e) { dispatch({ type: "failure", code: String(e) }); if (manual) useReleaseStore.setState({ visible: true }); }
  finally { checking = false; }
}
export async function installUpdate(flushNotes: () => Promise<void>) {
  if (installing || useReleaseStore.getState().update.phase !== "available") return;
  installing = true; dispatch({ type: "save" });
  let unlisten: (() => void) | undefined;
  try {
    try { await flushNotes(); } catch { throw "update_save"; }
    unlisten = await listen<{ phase: "downloading" | "verifying" | "installing"; received?: number; total?: number }>("update-progress", ({ payload }) => dispatch({ type: "progress", ...payload }));
    dispatch({ type: "progress", phase: "downloading" });
    await invoke("update_install");
  } catch (e) { dispatch({ type: "failure", code: String(e) }); }
  finally { unlisten?.(); installing = false; }
}
