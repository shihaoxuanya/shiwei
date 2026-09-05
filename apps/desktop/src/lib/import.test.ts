import { open } from "@tauri-apps/plugin-dialog";
import { chooseFiles } from "./import";
vi.mock("@tauri-apps/api/core", () => ({ invoke: vi.fn(), isTauri: () => true }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));
it("allows selecting PDFs through the real desktop dialog contract", async () => {
  vi.mocked(open).mockResolvedValue(["C:\\samples\\恢复记录.PDF"]);
  expect(await chooseFiles()).toEqual(["C:\\samples\\恢复记录.PDF"]);
  expect(open).toHaveBeenCalledWith(expect.objectContaining({ multiple: true, directory: false, filters: expect.arrayContaining([expect.objectContaining({ extensions: expect.arrayContaining(["pdf"]) })]) }));
});
