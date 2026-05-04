import { Fragment, useState, type ReactNode } from "react";
import type { ChatTurn, DashboardPage } from "../lib/types";
import { ViewChip } from "./ViewChip";

type Props = {
  turns: ChatTurn[];
  pages: DashboardPage[];
  disabled: boolean;
  sending: boolean;
  onSend: (message: string) => void;
  onOpenPage: (pageId: string) => void;
};

export function ChatSidebar({
  turns,
  pages,
  disabled,
  sending,
  onSend,
  onOpenPage,
}: Props) {
  const [input, setInput] = useState("");

  const submit = () => {
    const msg = input.trim();
    if (!msg || disabled || sending) return;
    onSend(msg);
    setInput("");
  };

  const pageByIdTitle = (id: string): string => {
    const p = pages.find((x) => x.id === id);
    if (!p) return "VIEW";
    const num = String(p.position + 1).padStart(2, "0");
    return `${num} ${p.title.toUpperCase()}`;
  };

  return (
    <aside className="flex h-full w-full shrink-0 flex-col bg-neutral-100">
      <div className="hairline border-b px-4 py-3">
        <div className="small-caps text-xs text-neutral-500">chat</div>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-2">
        {turns.length === 0 ? (
          <div className="mt-6 text-xs text-neutral-500">
            Ask Cerno a question about your data.
          </div>
        ) : (
          turns.map((turn) => (
            <div key={turn.id} className="hairline border-b py-4">
              <div className="small-caps mb-1 text-[10px] text-neutral-500">
                user
              </div>
              <div className="mb-4 text-sm text-ink">
                <MarkdownText text={turn.user_message} />
              </div>
              <div className="small-caps mb-1 text-[10px] text-ember">
                cerno
              </div>
              <div className="text-sm text-ink">
                <MarkdownText
                  text={
                    turn.assistant_message ??
                    (turn.state === "failed" ? "(failed)" : "\u2026")
                  }
                />
              </div>
              {turn.spawned_page_id ? (
                <div className="mt-3">
                  <ViewChip
                    label={pageByIdTitle(turn.spawned_page_id)}
                    onClick={() => onOpenPage(turn.spawned_page_id!)}
                  />
                </div>
              ) : null}
            </div>
          ))
        )}
      </div>
      <div className="hairline border-t bg-neutral-100 px-4 py-3">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              submit();
            }
          }}
          placeholder={disabled ? "upload a file to start" : "ask\u2026"}
          disabled={disabled}
          rows={3}
          className="hairline w-full resize-none border bg-white p-2 font-sans text-sm text-ink focus:outline-none focus:ring-1 focus:ring-ember disabled:opacity-40"
        />
        <div className="mt-2 flex items-center justify-between">
          <div className="text-[10px] text-neutral-400">{"\u2318 + enter"}</div>
          <button
            type="button"
            onClick={submit}
            disabled={disabled || sending || !input.trim()}
            className="small-caps bg-ember px-3 py-1 text-xs text-white hover:bg-ember-hover disabled:cursor-not-allowed disabled:opacity-40"
          >
            {sending ? "sending\u2026" : "send"}
          </button>
        </div>
      </div>
    </aside>
  );
}

function MarkdownText({ text }: { text: string }) {
  const blocks = parseMarkdownBlocks(text);
  return (
    <div className="space-y-2 leading-6">
      {blocks.map((block, index) => {
        if (block.kind === "code") {
          return (
            <pre
              key={index}
              className="overflow-x-auto border border-neutral-300 bg-white p-2 font-mono text-xs leading-5 text-ink"
            >
              {block.lines.join("\n")}
            </pre>
          );
        }
        if (block.kind === "list") {
          return (
            <ul key={index} className="list-disc space-y-1 pl-5">
              {block.lines.map((line, lineIndex) => (
                <li key={lineIndex}>{renderInlineMarkdown(line)}</li>
              ))}
            </ul>
          );
        }
        if (block.kind === "ordered-list") {
          return (
            <ol key={index} className="list-decimal space-y-1 pl-5">
              {block.lines.map((line, lineIndex) => (
                <li key={lineIndex}>{renderInlineMarkdown(line)}</li>
              ))}
            </ol>
          );
        }
        if (block.kind === "heading") {
          return (
            <div key={index} className="font-mono text-sm text-ink">
              {renderInlineMarkdown(block.lines[0] ?? "")}
            </div>
          );
        }
        return (
          <p key={index} className="whitespace-pre-wrap">
            {renderInlineMarkdown(block.lines.join("\n"))}
          </p>
        );
      })}
    </div>
  );
}

type MarkdownBlock = {
  kind: "paragraph" | "heading" | "list" | "ordered-list" | "code";
  lines: string[];
};

function parseMarkdownBlocks(text: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  let paragraph: string[] = [];
  let code: string[] | null = null;

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    blocks.push({ kind: "paragraph", lines: paragraph });
    paragraph = [];
  };

  for (const line of lines) {
    if (line.trim().startsWith("```")) {
      if (code) {
        blocks.push({ kind: "code", lines: code });
        code = null;
      } else {
        flushParagraph();
        code = [];
      }
      continue;
    }
    if (code) {
      code.push(line);
      continue;
    }
    if (!line.trim()) {
      flushParagraph();
      continue;
    }
    const heading = line.match(/^#{1,4}\s+(.+)$/);
    if (heading) {
      flushParagraph();
      blocks.push({ kind: "heading", lines: [heading[1] ?? ""] });
      continue;
    }
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    if (bullet) {
      flushParagraph();
      const previous = blocks[blocks.length - 1];
      if (previous?.kind === "list") previous.lines.push(bullet[1] ?? "");
      else blocks.push({ kind: "list", lines: [bullet[1] ?? ""] });
      continue;
    }
    const ordered = line.match(/^\s*\d+\.\s+(.+)$/);
    if (ordered) {
      flushParagraph();
      const previous = blocks[blocks.length - 1];
      if (previous?.kind === "ordered-list") previous.lines.push(ordered[1] ?? "");
      else blocks.push({ kind: "ordered-list", lines: [ordered[1] ?? ""] });
      continue;
    }
    paragraph.push(line);
  }

  if (code) blocks.push({ kind: "code", lines: code });
  flushParagraph();
  return blocks.length > 0 ? blocks : [{ kind: "paragraph", lines: [""] }];
}

function renderInlineMarkdown(text: string): ReactNode {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*)/g);
  return parts.map((part, index) => {
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={index} className="bg-white px-1 font-mono text-xs">
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    return <Fragment key={index}>{part}</Fragment>;
  });
}
