import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { invoke } from "@tauri-apps/api/core";
import { ReleaseInfrastructure, ReleaseSettings } from "./ReleaseInfrastructure";
import { initialUpdate, useReleaseStore, installUpdate, checkForUpdate, refreshReleaseStatus, setAnalyticsConsent } from "../lib/updates";

vi.mock("@tauri-apps/api/core", () => ({ isTauri: () => true, invoke: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn().mockResolvedValue(() => {}) }));
beforeEach(() => {
  vi.clearAllMocks();
  useReleaseStore.setState({ update: initialUpdate, visible: false, privacyError: "", privacySaving: false, privacyLoaded: false, status: { version: "0.3.0", channel: "stable", updaterConfigured: false, analytics: { enabled: false, configured: false, choice: "disabled", needsChoice: false } } });
  vi.mocked(invoke).mockImplementation(async (command) => command === "release_status" ? useReleaseStore.getState().status : undefined);
});
it("shows an existing disabled preference and honest unconfigured status", async () => {
  render(<ReleaseSettings />);
  expect(screen.getByRole("switch", { name: "帮助改进拾微" })).not.toBeChecked();
  expect(screen.getByText("版本 0.3.0")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "检查更新" })).toBeDisabled();
  expect(screen.getByText("当前内测版暂不支持在线更新。")).toBeInTheDocument();
  await waitFor(() => expect(invoke).toHaveBeenCalledWith("release_status"));
});
it("shows the saved enabled state with disclosure, without a consent dialog", async () => {
  useReleaseStore.setState({ status: { version: "0.3.1", channel: "stable", updaterConfigured: false, analytics: { enabled: true, configured: true, choice: "enabled" } } });
  render(<ReleaseSettings />);
  await waitFor(() => expect(screen.getByRole("switch", { name: "帮助改进拾微" })).toBeChecked());
  expect(screen.getByText("上传基础使用统计和错误信息，帮助改善体验。不收集资料、笔记、聊天内容或密钥，可随时关闭。")).toBeInTheDocument();
  expect(screen.getByText(/默认开启，保留已有关闭选择/)).toBeInTheDocument();
  expect(screen.getByText("统计详情").closest("details")).not.toHaveAttribute("open");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  await waitFor(() => expect(invoke).toHaveBeenCalledWith("release_status"));
  expect(invoke).not.toHaveBeenCalledWith("analytics_consent", expect.anything());
});
it("keeps the opt-out control immediately reachable", async () => {
  useReleaseStore.setState({ status: { version: "0.3.1", channel: "stable", updaterConfigured: false, analytics: { enabled: true, configured: true, choice: "enabled" } } });
  render(<ReleaseSettings />);
  await waitFor(() => expect(screen.getByRole("switch", { name: "帮助改进拾微" })).toBeEnabled());
  fireEvent.click(screen.getByRole("switch", { name: "帮助改进拾微" }));
  await waitFor(() => expect(invoke).toHaveBeenCalledWith("analytics_consent", { enabled: false }));
});
it("shows native default-on state without participation buttons or a blocking dialog", async () => {
  vi.mocked(invoke).mockImplementation(async (command) => command === "release_status"
    ? { ...useReleaseStore.getState().status, analytics: { enabled: true, configured: true, choice: "enabled", needsChoice: false } }
    : undefined);
  render(<ReleaseSettings />);
  await waitFor(() => expect(screen.getByRole("switch", { name: "帮助改进拾微" })).toBeChecked());
  expect(screen.queryByRole("button", { name: "参与基础使用统计" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "暂不参与" })).not.toBeInTheDocument();
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(invoke).not.toHaveBeenCalledWith("analytics_consent", expect.anything());
});
it("accepts the native enabled state without requiring a legacy choice marker", async () => {
  vi.mocked(invoke).mockResolvedValue({ ...useReleaseStore.getState().status, analytics: { enabled: true, configured: true } });
  render(<ReleaseSettings section="privacy" />);
  await waitFor(() => expect(screen.getByRole("switch", { name: "帮助改进拾微" })).toBeChecked());
  expect(useReleaseStore.getState().status.analytics.enabled).toBe(true);
});
it("keeps the switch off and disabled while loading or when native status fails", async () => {
  let rejectStatus: (reason: Error) => void = () => {};
  vi.mocked(invoke).mockImplementation(() => new Promise((_resolve, reject) => { rejectStatus = reject; }));
  render(<ReleaseSettings section="privacy" />);
  expect(screen.getByRole("switch", { name: "帮助改进拾微" })).not.toBeChecked();
  expect(screen.getByRole("switch", { name: "帮助改进拾微" })).toBeDisabled();
  rejectStatus(new Error("unreadable"));
  await waitFor(() => expect(useReleaseStore.getState().privacyLoaded).toBe(false));
  expect(screen.getByRole("switch", { name: "帮助改进拾微" })).not.toBeChecked();
  expect(invoke).not.toHaveBeenCalledWith("analytics_consent", expect.anything());
});
it("separates privacy and about sections for settings tabs", async () => {
  const { rerender } = render(<ReleaseSettings section="privacy" />);
  expect(screen.getByRole("heading", { name: "基础使用统计" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "关于拾微" })).not.toBeInTheDocument();
  rerender(<ReleaseSettings section="about" />);
  expect(screen.getByRole("heading", { name: "关于拾微" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "基础使用统计" })).not.toBeInTheDocument();
  await waitFor(() => expect(invoke).toHaveBeenCalledWith("release_status"));
});
it("failed consent persistence stays off and never blocks update checks", async () => {
  vi.mocked(invoke).mockImplementation(async (command) => {
    if (command === "analytics_consent") throw new Error("write failed");
    if (command === "update_check") return { status: "unconfigured" };
    return useReleaseStore.getState().status;
  });
  await setAnalyticsConsent(true);
  expect(useReleaseStore.getState().status.analytics.enabled).toBe(false);
  expect(useReleaseStore.getState().privacyError).toMatch(/保持关闭/);
  await checkForUpdate();
  expect(invoke).toHaveBeenCalledWith("update_check");
});
it("a late status response cannot re-enable a newly disabled choice", async () => {
  let resolveOld: (value: unknown) => void = () => {};
  vi.mocked(invoke).mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }));
  const old = refreshReleaseStatus();
  vi.mocked(invoke).mockImplementation(async (command) => command === "release_status" ? { ...useReleaseStore.getState().status, analytics: { enabled: false, configured: true, choice: "disabled" } } : undefined);
  await setAnalyticsConsent(false);
  resolveOld({ ...useReleaseStore.getState().status, analytics: { enabled: true, configured: true, choice: "enabled" } });
  await old;
  expect(useReleaseStore.getState().status.analytics.enabled).toBe(false);
  expect(useReleaseStore.getState().status.analytics.choice).toBe("disabled");
});
it("signature failures offer retry, never a bypass installation", () => {
  useReleaseStore.setState({ visible: true, update: { ...initialUpdate, phase: "error", error: "update_signature" } });
  render(<ReleaseInfrastructure flushNotes={async () => {}} />);
  expect(screen.getByRole("alert")).toHaveTextContent("已拒绝安装");
  expect(screen.queryByRole("button", { name: "立即更新" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "仍然安装" })).not.toBeInTheDocument();
});
it("available updates do not install until explicit confirmation", () => {
  useReleaseStore.setState({ visible: true, update: { ...initialUpdate, phase: "available", version: "0.3.1", notes: "修复问题" } });
  render(<ReleaseInfrastructure flushNotes={async () => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "稍后" }));
  expect(invoke).not.toHaveBeenCalledWith("update_install");
  expect(useReleaseStore.getState().visible).toBe(false);
});
it("mandatory updates require explicit installation and cannot be dismissed with Escape", () => {
  useReleaseStore.setState({ visible:true, update:{ ...initialUpdate, phase:"available", version:"0.3.1", mandatory:true } });
  render(<ReleaseInfrastructure flushNotes={async () => {}} />);
  expect(screen.queryByRole("button", {name:"稍后"})).not.toBeInTheDocument();
  fireEvent.keyDown(screen.getByRole("dialog"), {key:"Escape"});
  expect(useReleaseStore.getState().visible).toBe(true);
  expect(invoke).not.toHaveBeenCalledWith("update_install");
});
it("failed mandatory updates do not permanently lock local knowledge", () => {
  useReleaseStore.setState({ visible:true, update:{ ...initialUpdate, phase:"error", error:"update_withdrawn", mandatory:true } });
  render(<ReleaseInfrastructure flushNotes={async () => {}} />);
  expect(screen.queryByRole("button", {name:"立即更新"})).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", {name:"稍后"}));
  expect(useReleaseStore.getState().visible).toBe(false);
});
it("failed note save prevents download and install", async () => {
  useReleaseStore.setState({ update: { ...initialUpdate, phase: "available" } });
  await installUpdate(async () => { throw new Error("private note save failure"); });
  expect(invoke).not.toHaveBeenCalledWith("update_install");
  expect(useReleaseStore.getState().update.error).toBe("update_save");
});
it("duplicate check and install clicks are locked", async () => {
  let finish: (value: unknown) => void = () => {};
  vi.mocked(invoke).mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
  const first = checkForUpdate(); const duplicate = checkForUpdate();
  expect(invoke).toHaveBeenCalledTimes(1); finish({ status: "current" }); await Promise.all([first, duplicate]);
  useReleaseStore.setState({ update: { ...initialUpdate, phase: "available" } });
  let save: () => void = () => {};
  const flush = vi.fn(() => new Promise<void>((resolve) => { save = resolve; }));
  const a = installUpdate(flush); const b = installUpdate(flush); expect(flush).toHaveBeenCalledTimes(1);
  save(); await waitFor(() => expect(invoke).toHaveBeenCalledWith("update_install")); finish(undefined); await Promise.all([a, b]);
});
