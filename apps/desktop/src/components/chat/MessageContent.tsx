import { memo, useState, type ReactNode } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy } from "lucide-react";
import type { Root, RootContent, PhrasingContent } from "mdast";
import type { Citation } from "../../lib/chat";
import { normalizeNumericRanges } from "../../lib/answer-text";

export function CopyButton({
  text,
  label = "复制回答",
}: {
  text: string;
  label?: string;
}) {
  const [state, setState] = useState<"idle" | "copied" | "error">("idle");
  return (
    <button
      className="chat-text-action"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setState("copied");
        } catch {
          setState("error");
        }
      }}
      aria-label={label}
      title={state === "error" ? "复制失败，请选中文字后使用系统复制快捷键" : label}
    >
      {state === "copied" ? <Check size={14} /> : <Copy size={14} />}
      <span role="status">
        {state === "copied"
          ? "已复制"
          : state === "error"
            ? "请手动复制"
            : label}
      </span>
    </button>
  );
}

function CodeBlock({ children, text }: { children: ReactNode; text: string }) {
  return (
    <div className="chat-code">
      <div className="chat-code-toolbar">
        <span>代码</span>
        <CopyButton text={text} label="复制代码" />
      </div>
      <pre>{children}</pre>
    </div>
  );
}

export const MessageContent = memo(function MessageContent({
  text,
  citations = [],
  onCitation,
}: {
  text: string;
  citations?: Citation[];
  onCitation?: (citation: Citation) => void;
}) {
  const verified = new Map(citations.map((c) => [c.citationId, c]));
  const citationLinks = () => (tree: Root) => {
    const visit = (parent: Root | RootContent) => {
      if (
        !("children" in parent) ||
        parent.type === "link" ||
        parent.type === "linkReference"
      )
        return;
      const children = parent.children as RootContent[];
      for (let index = 0; index < children.length; index++) {
        const child = children[index];
        if (child.type !== "text") {
          visit(child);
          continue;
        }
        const parts: PhrasingContent[] = [];
        let offset = 0;
        for (const match of child.value.matchAll(/\[([SN]\d+)\]/g)) {
          if (!verified.has(match[1])) continue;
          if (match.index! > offset)
            parts.push({
              type: "text",
              value: child.value.slice(offset, match.index),
            });
          parts.push({
            type: "link",
            url: `#shiwei-citation-${match[1]}`,
            children: [{ type: "text", value: match[0] }],
          });
          offset = match.index! + match[0].length;
        }
        if (!offset) continue;
        if (offset < child.value.length)
          parts.push({ type: "text", value: child.value.slice(offset) });
        children.splice(index, 1, ...parts);
        index += parts.length - 1;
      }
    };
    visit(tree);
  };
  return (
    <div className="chat-markdown">
      <Markdown
        remarkPlugins={[[remarkGfm, { singleTilde: false }], citationLinks]}
        skipHtml
        // Untrusted model output cannot fetch remote images or navigate the webview.
        // Links are copyable text; opening files is only through verified source cards.
        components={{
          img: ({ alt }) => (
            <span className="text-muted">
              [图片：{alt || "未加载外部图片"}]
            </span>
          ),
          a: ({ children, href }) => {
            const citation = verified.get(
              href?.replace(/^#shiwei-citation-/, "") ?? "",
            );
            return citation && onCitation ? (
              <button
                className="chat-inline-cite"
                title={`查看出处：${citation.sourceFilename}`}
                onClick={() => onCitation(citation)}
              >
                {children}
              </button>
            ) : (
              <span className="chat-link" title={href}>
                {children}
                {href && <CopyButton text={href} label="复制链接" />}
              </span>
            );
          },
          pre: ({ children, node }) => (
            <CodeBlock
              text={
                node?.children
                  .map((child) =>
                    child.type === "element"
                      ? child.children
                          .map((n) => (n.type === "text" ? n.value : ""))
                          .join("")
                      : child.type === "text"
                        ? child.value
                        : "",
                  )
                  .join("") ?? ""
              }
            >
              {children}
            </CodeBlock>
          ),
          table: ({ children }) => (
            <div
              className="chat-table-scroll"
              tabIndex={0}
              aria-label="回答中的表格"
            >
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {normalizeNumericRanges(text)}
      </Markdown>
    </div>
  );
});
