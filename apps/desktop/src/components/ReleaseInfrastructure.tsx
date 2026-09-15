import { openUrl } from "@tauri-apps/plugin-opener";
import { Button } from "./ui/button";
import { APP_VERSION } from "../release-version";

export function ReleaseSettings({ section = "all" }: { section?: "all" | "privacy" | "about" }) {
  if (section === "privacy") return null;
  return <section aria-label="关于" className="rounded-2xl border border-line bg-panel p-6">
    <h2 className="text-base font-semibold">关于拾微</h2>
    <p className="mt-3 font-serif text-xl">拾微 <span className="ml-2 font-sans text-sm text-muted">版本 {APP_VERSION}</span></p>
    <p className="mt-3 text-sm leading-6 text-muted">个人本地知识库 · MIT 开源。不上传使用统计或错误日志，不在后台检查或安装更新。</p>
    <p className="mt-2 text-sm leading-6 text-muted">联网模型由你自行选择。新版本可前往 GitHub 手动下载，访问 GitHub 将打开浏览器。</p>
    <Button variant="secondary" className="mt-4" onClick={() => { void openUrl("https://github.com/shihaoxuanya/shiwei-releases/releases").catch(() => {}); }}>前往 GitHub 下载</Button>
  </section>;
}
