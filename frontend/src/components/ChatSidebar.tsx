import { Fragment, useEffect, useState, type ReactNode } from "react";
import { listTurnMessages } from "../lib/api";
import type { ChatMessage, ChatTurn, DashboardPage } from "../lib/types";
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
  const [messagesByTurn, setMessagesByTurn] = useState<
    Record<string, ChatMessage[]>
  >({});
  const [openTraceByTurn, setOpenTraceByTurn] = useState<Record<string, boolean>>(
    {},
  );

  useEffect(() => {
    let cancelled = false;

    if (turns.length === 0) {
      setMessagesByTurn({});
      return;
    }

    const turnIds = new Set(turns.map((turn) => turn.id));
    setMessagesByTurn((current) => {
      const next: Record<string, ChatMessage[]> = {};
      for (const [turnId, messages] of Object.entries(current)) {
        if (turnIds.has(turnId)) next[turnId] = messages;
      }
      return next;
    });
    setOpenTraceByTurn((current) => {
      const next: Record<string, boolean> = {};
      for (const [turnId, open] of Object.entries(current)) {
        if (turnIds.has(turnId)) next[turnId] = open;
      }
      return next;
    });

    Promise.all(
      turns.map(async (turn) => {
        const messages = await listTurnMessages(turn.id);
        return [turn.id, messages] as const;
      }),
    )
      .then((entries) => {
        if (cancelled) return;
        setMessagesByTurn(Object.fromEntries(entries));
      })
      .catch((err: unknown) => {
        console.warn("Failed to load chat message trace", err);
      });

    return () => {
      cancelled = true;
    };
  }, [turns]);

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

  const toggleTrace = (turnId: string) => {
    setOpenTraceByTurn((current) => ({
      ...current,
      [turnId]: !current[turnId],
    }));
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
          turns.map((turn) => {
            const messages = messagesByTurn[turn.id] ?? [];
            const traceItems = messages
              .map((message, index) => ({
                message,
                result: findToolResult(messages, index),
              }))
              .filter(
                ({ message }) => message.role === "assistant" && message.tool_name,
              );

            return (
              <div key={turn.id} className="hairline border-b py-4">
                <div className="mb-4 flex justify-end">
                  <div className="max-w-[92%] border border-orange-200 bg-orange-50 px-3 py-2 text-sm text-ink shadow-[inset_0_1px_0_rgba(255,255,255,0.75)]">
                    <MarkdownText text={turn.user_message} compact />
                  </div>
                </div>
                <div className="small-caps mb-1 text-[10px] text-ember">
                  cerno
                </div>
                {traceItems.length > 0 ? (
                  <TraceDisclosure
                    open={Boolean(openTraceByTurn[turn.id])}
                    items={traceItems}
                    onToggle={() => toggleTrace(turn.id)}
                  />
                ) : null}
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
            );
          })
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

type TraceItem = {
  message: ChatMessage;
  result: ChatMessage | null;
};

function TraceDisclosure({
  open,
  items,
  onToggle,
}: {
  open: boolean;
  items: TraceItem[];
  onToggle: () => void;
}) {
  const label = open ? "hide thinking & tools" : "show thinking & tools";

  return (
    <div className="mb-3">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="flex w-full items-center justify-between border border-neutral-200 bg-white px-2.5 py-2 text-left hover:border-orange-200 hover:bg-orange-50"
      >
        <span className="small-caps text-[10px] text-neutral-500">
          {label}
        </span>
        <span className="flex items-center gap-2">
          <span className="font-mono text-[10px] text-neutral-400">
            {items.length} {items.length === 1 ? "step" : "steps"}
          </span>
          <span className="text-xs text-ember">{open ? "\u2191" : "\u2193"}</span>
        </span>
      </button>
      {open ? (
        <div className="mt-2 space-y-2">
          {items.map(({ message, result }) => (
            <ToolCallCard key={message.id} message={message} result={result} />
          ))}
        </div>
      ) : null}
    </div>
  );
}

function ToolCallCard({
  message,
  result,
}: {
  message: ChatMessage;
  result: ChatMessage | null;
}) {
  return (
    <div className="border border-orange-200 bg-white px-3 py-2 shadow-[0_1px_0_rgba(14,14,14,0.04)]">
      <div className="flex items-start gap-2">
        <div className="mt-1 h-2 w-2 shrink-0 bg-ember" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-3">
            <div className="small-caps text-[10px] text-neutral-500">tool call</div>
            <div className="small-caps text-[10px] text-ember">
              {result ? "complete" : "running"}
            </div>
          </div>
          <div className="mt-0.5 truncate font-mono text-xs text-ink">
            {formatToolName(message.tool_name)}
          </div>
          {message.content.trim() ? (
            <div className="mt-2 border-l-2 border-orange-200 pl-2 text-[11px] leading-5 text-neutral-600">
              <div className="small-caps mb-1 text-[10px] text-neutral-400">
                thinking
              </div>
              <MarkdownText text={message.content} compact />
            </div>
          ) : null}
          {message.tool_args ? (
            <div className="mt-2 text-[11px] leading-5 text-neutral-600">
              {summarizeRecord(message.tool_args)}
            </div>
          ) : null}
          {result?.tool_result ? (
            <div className="mt-2 border-t border-neutral-200 pt-2 text-[11px] leading-5 text-neutral-500">
              {summarizeToolResult(result.tool_result)}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function findToolResult(
  messages: ChatMessage[],
  toolCallIndex: number,
): ChatMessage | null {
  for (let i = toolCallIndex + 1; i < messages.length; i += 1) {
    const message = messages[i];
    if (!message) continue;
    if (message.role === "assistant" && message.tool_name) return null;
    if (message.role === "tool") return message;
  }
  return null;
}

function formatToolName(name: string | null) {
  if (!name) return "tool";
  return name.replace(/_/g, " ");
}

function summarizeRecord(record: Record<string, unknown>) {
  const entries = Object.entries(record)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .slice(0, 3);
  if (entries.length === 0) return "No arguments";
  return entries
    .map(([key, value]) => `${key}: ${formatPreviewValue(value)}`)
    .join(" | ");
}

function summarizeToolResult(record: Record<string, unknown>) {
  if (typeof record.error === "string") return `Error: ${record.error}`;
  const keys = Object.keys(record).filter((key) => key !== "error");
  if (keys.length === 0) return "Result returned";
  return `Result: ${keys.slice(0, 4).join(", ")}`;
}

function formatPreviewValue(value: unknown) {
  let text: string;
  if (Array.isArray(value)) text = `${value.length} items`;
  else if (typeof value === "object" && value !== null) {
    const keys = Object.keys(value as Record<string, unknown>);
    text = keys.length > 0 ? `{${keys.slice(0, 3).join(", ")}}` : "{}";
  } else {
    text = String(value);
  }
  return text.length > 64 ? `${text.slice(0, 61)}...` : text;
}

function MarkdownText({
  text,
  compact = false,
}: {
  text: string;
  compact?: boolean;
}) {
  const blocks = parseMarkdownBlocks(text);
  return (
    <div className={compact ? "space-y-1 leading-6" : "space-y-2 leading-6"}>
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
        if (block.kind === "table") {
          const table = parseMarkdownTable(block.lines);
          return (
            <div
              key={index}
              className="overflow-x-auto border border-neutral-200 bg-white"
            >
              <table className="w-full border-collapse text-left text-xs">
                <thead className="bg-neutral-100 text-neutral-600">
                  <tr>
                    {table.header.map((cell, cellIndex) => (
                      <th
                        key={cellIndex}
                        className="border-b border-neutral-200 px-2 py-1 font-mono"
                      >
                        {renderInlineMarkdown(cell)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.map((row, rowIndex) => (
                    <tr key={rowIndex} className="border-t border-neutral-100">
                      {row.map((cell, cellIndex) => (
                        <td key={cellIndex} className="px-2 py-1 align-top">
                          {renderInlineMarkdown(cell)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
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
        if (block.kind === "quote") {
          return (
            <blockquote
              key={index}
              className="border-l-2 border-orange-200 pl-3 text-neutral-600"
            >
              {block.lines.map((line, lineIndex) => (
                <p key={lineIndex}>{renderInlineMarkdown(line)}</p>
              ))}
            </blockquote>
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
  kind:
    | "paragraph"
    | "heading"
    | "list"
    | "ordered-list"
    | "code"
    | "quote"
    | "table";
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

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? "";
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
    if (isMarkdownTableStart(lines, i)) {
      flushParagraph();
      const tableLines: string[] = [];
      while (i < lines.length && lines[i]?.includes("|") && lines[i]?.trim()) {
        tableLines.push(lines[i] ?? "");
        i += 1;
      }
      i -= 1;
      blocks.push({ kind: "table", lines: tableLines });
      continue;
    }
    const heading = line.match(/^#{1,4}\s+(.+)$/);
    if (heading) {
      flushParagraph();
      blocks.push({ kind: "heading", lines: [heading[1] ?? ""] });
      continue;
    }
    const quote = line.match(/^>\s?(.*)$/);
    if (quote) {
      flushParagraph();
      const previous = blocks[blocks.length - 1];
      if (previous?.kind === "quote") previous.lines.push(quote[1] ?? "");
      else blocks.push({ kind: "quote", lines: [quote[1] ?? ""] });
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

function isMarkdownTableStart(lines: string[], index: number) {
  const line = lines[index] ?? "";
  const next = lines[index + 1] ?? "";
  return line.includes("|") && isMarkdownTableDivider(next);
}

function isMarkdownTableDivider(line: string) {
  const cells = line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|");
  return (
    cells.length > 1 &&
    cells.every((cell) => /^:?-{3,}:?$/.test(cell.trim()))
  );
}

function parseMarkdownTable(lines: string[]) {
  const rows = lines
    .filter((line) => !isMarkdownTableDivider(line))
    .map((line) =>
      line
        .trim()
        .replace(/^\|/, "")
        .replace(/\|$/, "")
        .split("|")
        .map((cell) => cell.trim()),
    );
  const header = rows[0] ?? [];
  const body = rows.slice(1).map((row) =>
    header.map((_, index) => row[index] ?? ""),
  );
  return { header, rows: body };
}

function renderInlineMarkdown(text: string): ReactNode {
  const parts = text.split(
    /(\[[^\]]+\]\([^)]+\)|`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g,
  );
  return parts.map((part, index) => {
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link) {
      const href = sanitizeHref(link[2] ?? "");
      return (
        <a
          key={index}
          href={href}
          target="_blank"
          rel="noreferrer"
          className="text-ember underline decoration-orange-200 underline-offset-2"
        >
          {link[1]}
        </a>
      );
    }
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
    if (part.startsWith("*") && part.endsWith("*")) {
      return <em key={index}>{part.slice(1, -1)}</em>;
    }
    return <Fragment key={index}>{part}</Fragment>;
  });
}

function sanitizeHref(href: string) {
  if (/^(https?:|mailto:)/i.test(href)) return href;
  return "#";
}
