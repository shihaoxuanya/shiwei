import { render } from "@testing-library/react";
import { MessageContent } from "./MessageContent";
import { CitationChip, SourcePanel } from "./Sources";
import { normalizeNumericRanges } from "../../lib/answer-text";
import type { Citation } from "../../lib/chat";

it("preserves both numeric ranges instead of parsing them as strikethrough", () => {
  const { container } = render(<MessageContent text="单次全量迁移预计耗时38~50天，增量追平需7~14天。" />);
  expect(container).toHaveTextContent("38～50 天");
  expect(container).toHaveTextContent("7～14 天");
  expect(container.querySelector("del")).toBeNull();
  expect(container).not.toHaveTextContent("3850天");
  expect(container).not.toHaveTextContent("714天");
});

it.each([
  ["38~50天，7~14天，100~200GB", "38～50 天，7～14 天，100～200GB"],
  ["38\\~50天", "38～50 天"],
  ["-1.5~2.5小时", "-1.5～2.5 小时"],
  ["你好~ ~/data ~变量~ ~~删除~~", "你好~ ~/data ~变量~ ~~删除~~"],
  ["`38~50`\n```sh\nx=7~14\n```\n    100~200GB", "`38~50`\n```sh\nx=7~14\n```\n    100~200GB"],
  ["https://example.test/38~50 [原样](./7~14)", "https://example.test/38~50 [原样](./7~14)"],
])("normalizes only numeric prose ranges: %s", (raw, expected) => {
  expect(normalizeNumericRanges(raw)).toBe(expected);
  expect(normalizeNumericRanges(expected)).toBe(expected);
});

it("keeps explicit strikethrough and literal code, not single-tilde syntax", () => {
  const { container } = render(<MessageContent text={"~~删除~~ ~保留~ `38~50`"} />);
  expect(container.querySelector("del")).toHaveTextContent("删除");
  expect(container.querySelector("code")).toHaveTextContent("38~50");
  expect(container).toHaveTextContent("~保留~");
});

const noteCitation = {
  citationId: "N1", sourceType: "user_note", noteId: "note-1",
  sourceFilename: "2025年9月3日会议", snippet: "单次38~50天",
  mentionedDates: ["2025-09-03"], noteCreatedAt: "2026-09-05T05:44:00Z",
  noteUpdatedAt: "2026-09-05T06:44:00Z",
} as Citation;

it("does not append the save date to a note citation chip", () => {
  const { container } = render(<CitationChip citation={noteCitation} onSelect={() => {}} />);
  expect(container.textContent).toBe("[N1] 2025年9月3日会议");
});

it("labels mentioned dates separately from note creation/update dates in the drawer", () => {
  const { container } = render(<SourcePanel citation={noteCitation} onClose={() => {}} />);
  expect(container).toHaveTextContent("类型：笔记");
  expect(container).toHaveTextContent("记录中提及日期：2025年9月3日");
  expect(container).toHaveTextContent("创建于");
  expect(container).toHaveTextContent("更新于");
  expect(container).not.toHaveTextContent("事件日期");
});

it("does not guess a mentioned/event date when none was extracted", () => {
  const { container } = render(<SourcePanel citation={{ ...noteCitation, mentionedDates: undefined }} onClose={() => {}} />);
  expect(container).not.toHaveTextContent("记录中提及日期");
  expect(container).not.toHaveTextContent("事件日期");
  expect(container).toHaveTextContent("创建于");
});

it("retains file page locators in the main citation chip", () => {
  const { container } = render(<CitationChip citation={{ ...noteCitation, sourceType: "imported_file", pageNumber: 7 }} onSelect={() => {}} />);
  expect(container).toHaveTextContent("第 7 页");
});
