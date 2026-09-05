import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { invoke } from "@tauri-apps/api/core";
import { ReleaseInfrastructure, ReleaseSettings } from "./ReleaseInfrastructure";
import { initialUpdate, useReleaseStore, installUpdate, checkForUpdate } from "../lib/updates";

vi.mock("@tauri-apps/api/core", () => ({ isTauri: () => true, invoke: vi.fn() }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn().mockResolvedValue(() => {}) }));
beforeEach(() => {
  vi.clearAllMocks();
  useReleaseStore.setState({ update: initialUpdate, visible: false, privacyError: "", status: { version: "0.3.0", channel: "stable", updaterConfigured: false, analytics: { enabled: false, configured: false } } });
  vi.mocked(invoke).mockImplementation(async (command) => command === "release_status" ? useReleaseStore.getState().status : undefined);
});
it("shows version and default-off privacy with honest unconfigured status", async () => {
  render(<ReleaseSettings />);
  expect(screen.getByRole("switch", { name: "帮助改进拾微" })).not.toBeChecked();
  expect(screen.getByText("版本 0.3.0")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "检查更新" })).toBeDisabled();
  await waitFor(() => expect(invoke).toHaveBeenCalledWith("release_status"));
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
