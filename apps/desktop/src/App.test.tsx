import { fireEvent, render, screen } from "@testing-library/react";
import App, { CitationChip } from "./App";

describe("Phase 0 shell", () => {
  it("shows the core value and five focused destinations", async () => {
    render(<App />);

    expect(screen.getByText("你不用整理，", { exact: false })).toBeInTheDocument();
    for (const label of ["首页", "对话", "资料", "笔记", "设置"]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
    expect(await screen.findByText("浏览器预览")).toBeInTheDocument();
    expect(screen.queryByText("问问拾微")).not.toBeInTheDocument();
  });

  it("renders a visible worker status instead of failing silently", async () => {
    render(<App />);
    expect(await screen.findByText("浏览器预览")).toBeInTheDocument();
  });

  it("renders a citation with its verified source id and opens its detail action", () => {
    const onSelect = vi.fn();
    const citation = {
      citationId: "S1",
      documentId: "doc-1",
      chunkId: "chunk-1",
      sourceFilename: "Oracle恢复记录.md",
      snippet: "PDB 处于 MOUNTED 状态",
    };
    render(<CitationChip citation={citation} onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "[S1] Oracle恢复记录.md" }));
    expect(onSelect).toHaveBeenCalledWith(citation);
  });
});
