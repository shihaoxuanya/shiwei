import { useEffect, useRef } from "react";
import { isTauri } from "@tauri-apps/api/core";
import { Button } from "./ui/button";
import { checkForUpdate, downloadPercent, installUpdate, refreshReleaseStatus, setAnalyticsConsent, updateErrorCopy, useReleaseStore } from "../lib/updates";
import { installErrorReporting } from "../lib/analytics";

export function ReleaseSettings({ section = "all" }: { section?: "all" | "privacy" | "about" }) {
  const { status, update, privacyError, privacySaving, privacyLoaded } = useReleaseStore();
  useEffect(() => { void refreshReleaseStatus(); }, []);
  return <div className="space-y-5">
    {section !== "about" && <section aria-label="隐私" className="rounded-2xl border border-line bg-panel p-6">
      <h2 className="text-base font-semibold">基础使用统计</h2>
      <label className="mt-4 flex items-center justify-between gap-5 text-sm"><span>帮助改进拾微</span><input type="checkbox" role="switch" aria-label="帮助改进拾微" checked={privacyLoaded && status.analytics.enabled} disabled={!isTauri() || privacySaving || !privacyLoaded} onChange={(e) => void setAnalyticsConsent(e.target.checked)} className="size-5 accent-indigo" /></label>
      <p className="mt-3 text-sm leading-6 text-muted">上传基础使用统计和错误信息，帮助改善体验。不收集资料、笔记、聊天内容或密钥，可随时关闭。</p>
      <details className="mt-3 text-sm leading-6 text-muted"><summary className="cursor-pointer">统计详情</summary><p className="mt-2">默认开启，保留已有关闭选择。仅向拾微服务发送随机安装标识、版本、基础事件及数量、固定错误类型和受限模块位置；不发送文件名、路径、原始日志或完整错误堆栈。</p><p className="mt-2">随机安装标识是去标识化信息，不代表完全没有隐私影响。第一方服务加盐汇总，事件最多保留 90 天。关闭会取消未完成发送、清理待发事件，重新开启不补传；已接收的统计按保留期限清理，不会立即删除。关闭不影响本地功能或检查更新。</p></details>
      {!status.analytics.configured && <p className="mt-2 text-xs text-muted">此构建尚未配置统计服务，不会上传统计数据。</p>}
      {privacyError && <p role="alert" className="mt-3 text-sm text-red-700">{privacyError}</p>}
    </section>}
    {section !== "privacy" && <section aria-label="关于" className="rounded-2xl border border-line bg-panel p-6">
      <h2 className="text-base font-semibold">关于拾微</h2><p className="mt-3 font-serif text-xl">拾微 <span className="ml-2 font-sans text-sm text-muted">版本 {status.version}</span></p>
      <p className="mt-3 text-sm leading-6 text-muted">{!status.updaterConfigured ? "当前内测版暂不支持在线更新。" : `更新状态：${update.phase === "current" ? "已是最新版本" : update.phase === "checking" ? "正在检查…" : update.phase === "available" ? `发现新版本 ${update.version}` : update.phase === "error" ? "检查或更新失败，当前版本可继续使用" : "已启用签名验证 · 稳定版"}`}</p>
      <div className="mt-4 flex gap-3"><Button variant="secondary" disabled={!isTauri() || update.phase === "checking" || !status.updaterConfigured} onClick={() => void checkForUpdate(true)}>检查更新</Button>{update.phase === "available" && <Button onClick={() => useReleaseStore.getState().setVisible(true)}>查看更新</Button>}</div>
    </section>}
  </div>;
}

export function ReleaseInfrastructure({ flushNotes }: { flushNotes: () => Promise<void> }) {
  const { update, visible, setVisible } = useReleaseStore();
  const initialized = useRef(false);
  useEffect(() => {
    const dispose = installErrorReporting();
    const timer = window.setTimeout(() => {
      if (initialized.current) return;
      initialized.current = true;
      void refreshReleaseStatus().then(() => checkForUpdate());
    }, 3000);
    return () => { clearTimeout(timer); dispose(); };
  }, []);
  const busy = ["saving", "downloading", "verifying", "installing"].includes(update.phase);
  const required = update.mandatory === true && update.phase === "available";
  const close = useRef<HTMLButtonElement>(null);
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!visible) return;
    const previous = document.activeElement as HTMLElement;
    (close.current ?? dialog.current)?.focus();
    return () => { if (previous?.isConnected) previous.focus(); };
  }, [visible]);
  if (!visible) return null;
  const percent = downloadPercent(update);
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/20 p-6" onKeyDown={(e) => { if (e.key === "Escape" && !busy && !required) setVisible(false); }}>
    <section ref={dialog} tabIndex={-1} role="dialog" aria-modal="true" aria-labelledby="update-title" onKeyDown={(e) => {
      if (e.key !== "Tab") return;
      const buttons = Array.from(dialog.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? []);
      if (!buttons.length) { e.preventDefault(); dialog.current?.focus(); return; }
      const first = buttons[0], last = buttons[buttons.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    }} className="w-full max-w-lg rounded-2xl border border-line bg-paper p-7 shadow-xl">
      <h2 id="update-title" className="font-serif text-2xl">{update.phase === "error" ? "更新未完成" : busy ? "正在更新拾微" : "发现新版本"}</h2>
      {update.version && <p className="mt-3 text-sm text-indigo">v{update.version}</p>}
      {required && <p role="status" className="mt-4 text-sm leading-7">需要更新到最新版才能继续使用。更新前会先保存笔记；不会自动安装或降级。</p>}
      {!busy && update.phase !== "error" && <p className="mt-4 max-h-60 overflow-auto whitespace-pre-wrap text-sm leading-7">{update.notes || "包含稳定性改进。"}</p>}
      {busy && <div role="status" className="mt-5 text-sm"><p>{update.phase === "saving" ? "正在保存笔记…" : update.phase === "verifying" ? "正在验证更新签名…" : update.phase === "installing" ? "正在启动安装，完成后将重新打开拾微…" : `正在下载更新${percent === null ? "…" : ` ${percent}%`}`}</p>{update.phase === "downloading" && <progress aria-label="更新下载进度" max={100} value={percent ?? undefined} className="mt-4 w-full accent-indigo" />}</div>}
      {update.phase === "error" && <p role="alert" className="mt-5 text-sm leading-7">{updateErrorCopy[update.error ?? ""] ?? "更新暂时失败，请稍后重试。"} 当前版本仍然可以继续使用。</p>}
      <div className="mt-6 flex justify-end gap-3">{!required && <Button ref={close} variant="secondary" disabled={busy} onClick={() => setVisible(false)}>稍后</Button>}{update.phase === "available" && <Button onClick={() => void installUpdate(flushNotes)}>立即更新</Button>}{update.phase === "error" && <Button onClick={() => void checkForUpdate(true)}>稍后重试</Button>}</div>
    </section>
  </div>;
}
