import { fireEvent, render, screen } from "@testing-library/react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { invoke } from "@tauri-apps/api/core";
import { ReleaseSettings } from "./ReleaseInfrastructure";
import { APP_VERSION } from "../release-version";
vi.mock("@tauri-apps/plugin-opener", () => ({ openUrl: vi.fn().mockResolvedValue(undefined) }));
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn() }));
beforeEach(() => vi.clearAllMocks());
it("about makes no background IPC or browser requests", () => {
  render(<ReleaseSettings />);
  expect(screen.getByText(`版本 ${APP_VERSION}`)).toBeTruthy();
  expect(screen.queryByRole("switch")).toBeNull();
  expect(screen.queryByText("检查更新")).toBeNull();
  expect(invoke).not.toHaveBeenCalled();
  expect(openUrl).not.toHaveBeenCalled();
});
it("only an explicit click opens public downloads", () => {
  render(<ReleaseSettings />);
  fireEvent.click(screen.getByRole("button", { name: "前往 GitHub 下载" }));
  expect(openUrl).toHaveBeenCalledWith("https://github.com/shihaoxuanya/shiwei-releases/releases");
  expect(invoke).not.toHaveBeenCalled();
});
it("legacy privacy section cannot expose telemetry", () => {
  const { container } = render(<ReleaseSettings section="privacy" />);
  expect(container.textContent).toBe("");
  expect(invoke).not.toHaveBeenCalled();
});
