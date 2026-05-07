import { Fragment, useEffect, useRef, useState, type ReactNode } from "react";
import type {
  ChatArtifact,
  ChatMessage,
  ChatTurn,
  DataDoc,
  DiscoveryResponse,
  DiscoveryStatus,
  FileRecord,
  Widget,
} from "../lib/types";
import { WidgetRenderer } from "./WidgetRenderer";

type Props = {
  turns: ChatTurn[];
  messagesByTurn: Record<string, ChatMessage[]>;
  artifactsByTurn: Record<string, ChatArtifact[]>;
  activeTurnId: string | null;
  files: FileRecord[];
  discovery: DiscoveryResponse | null;
  dataDoc: DataDoc | null;
  discoveryStatus: DiscoveryStatus;
  disabled: boolean;
  sending: boolean;
  liveChat: LiveChatState | null;
  onSend: (message: string) => void;
  onOpenInsights: () => void;
  onOpenFiles: () => void;
};

type LiveChatState = {
  turnId: string | null;
  userMessage: string;
  assistantText: string;
  reasoningText: string;
  tools: Array<{
    callId: string;
    name: string;
    argsText: string;
    args?: Record<string, unknown>;
    result?: Record<string, unknown>;
  }>;
  error: string | null;
};

export function ChatSidebar({
  turns,
  messagesByTurn,
  artifactsByTurn,
  activeTurnId,
  dataDoc,
  disabled,
  sending,
  liveChat,
  onSend,
}: Props) {
  const [input, setInput] = useState("");
  const [pendingMessage, setPendingMessage] = useState<string | null>(null);
  const [openTraceByTurn, setOpenTraceByTurn] = useState<Record<string, boolean>>(
    {},
  );
  const scrollRef = useRef<HTMLDivElement>(null);
  const visibleTurns = activeTurnId
    ? turns.filter((turn) => turn.id === activeTurnId)
    : [];
  const showLiveChat = Boolean(
    liveChat && (liveChat.turnId === activeTurnId || (!activeTurnId && visibleTurns.length === 0)),
  );

  useEffect(() => {
    const turnIds = new Set(turns.map((turn) => turn.id));
    setOpenTraceByTurn((current) => {
      const next: Record<string, boolean> = {};
      for (const [traceKey, open] of Object.entries(current)) {
        const turnId = traceKey.split(":")[0] ?? traceKey;
        if (turnIds.has(turnId)) next[traceKey] = open;
      }
      return next;
    });
  }, [turns]);

  const submit = () => {
    const msg = input.trim();
    if (!msg || disabled || sending) return;
    setPendingMessage(msg);
    onSend(msg);
    setInput("");
  };

  // Clear pending message when turns update (meaning the server responded)
  useEffect(() => {
    if (!sending && pendingMessage) {
      setPendingMessage(null);
    }
  }, [sending, turns]);

  // Auto-scroll to bottom when sending or turns change
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [visibleTurns, sending, pendingMessage]);

  const toggleTrace = (turnId: string) => {
    setOpenTraceByTurn((current) => ({
      ...current,
      [turnId]: !current[turnId],
    }));
  };

  return (
    <section className="flex h-full w-full flex-col bg-[#fffdf9]">
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-8 py-6">
        {visibleTurns.length === 0 && !pendingMessage && !showLiveChat ? (
          <div className="mt-4 flex flex-col items-center justify-center text-center">
            <div className="mb-6 font-mono text-xl text-neutral-400">
              How can I help you analyze this workspace?
            </div>
            {dataDoc?.starter_questions && dataDoc.starter_questions.length > 0 && (
              <div className="flex flex-col gap-2 w-full max-w-2xl">
                {dataDoc.starter_questions.map((question) => (
                  <button
                    key={question}
                    type="button"
                    onClick={() => {
                      if (disabled || sending) return;
                      setPendingMessage(question);
                      onSend(question);
                    }}
                    className="border border-neutral-200 bg-white px-4 py-3 text-left text-sm text-neutral-700 hover:border-ember hover:text-ember transition"
                  >
                    {question}
                  </button>
                ))}
              </div>
            )}
          </div>
        ) : (
          visibleTurns.map((turn) => {
            const persistedMessages = messagesByTurn[turn.id] ?? [];
            const messages =
              persistedMessages.length > 0
                ? persistedMessages
                : fallbackMessages(turn);
            const widgetArtifacts = widgetArtifactsFromArtifacts(
              artifactsByTurn[turn.id] ?? [],
            );
            const traceItems = messages
              .map((message, index) => ({
                message,
                result: findToolResult(messages, index),
              }))
              .filter(
                ({ message }) => message.role === "assistant" && message.tool_name,
              );
            const visibleMessages = messages.filter(
              (message) => message.role !== "tool" && !message.tool_name,
            );

            return (
              <div key={turn.id} className="hairline border-b py-4">
                {messages.length === 0 && turn.state !== "complete" ? <ThinkingIndicator /> : null}
                {visibleMessages.map((message, index) => {
                  if (message.role === "user") {
                    return <UserBubble key={message.id} text={message.content} />;
                  }
                  if (message.role !== "assistant") return null;
                  const traceKey = `${turn.id}:${message.id}`;
                  const assistantTraceItems = traceItemsForAssistant(
                    message,
                    messages,
                    traceItems,
                  );
                  const assistantWidgets = widgetArtifactsForMessage(
                    message,
                    messages,
                    widgetArtifacts,
                  );
                  return (
                    <Fragment key={message.id}>
                      {assistantTraceItems.length > 0 ? (
                        <TraceDisclosure
                          open={Boolean(openTraceByTurn[traceKey])}
                          items={assistantTraceItems}
                          onToggle={() => toggleTrace(traceKey)}
                        />
                      ) : null}
                      <AssistantBubble text={message.content} />
                      {assistantWidgets.length > 0 ? (
                        <WidgetGrid
                          widgets={assistantWidgets.map((item) => item.widget)}
                          idPrefix={`${turn.id}:${message.id}:${index}`}
                        />
                      ) : null}
                    </Fragment>
                  );
                })}
              </div>
            );
          })
        )}

        {showLiveChat && liveChat ? <LiveChatBlock liveChat={liveChat} /> : null}
        {!showLiveChat && pendingMessage && sending ? (
          <LiveChatBlock
            liveChat={{
              turnId: activeTurnId,
              userMessage: pendingMessage,
              assistantText: "",
              reasoningText: "",
              tools: [],
              error: null,
            }}
          />
        ) : null}
      </div>
      <div className="hairline border-t bg-white/90 px-8 py-4">
        <div className="flex flex-col border bg-white focus-within:ring-1 focus-within:ring-ember transition-shadow">
          <textarea
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder={disabled ? "upload a file to start" : "ask\u2026"}
            disabled={disabled}
            rows={2}
            className="w-full resize-none bg-transparent p-4 font-sans text-base text-ink focus:outline-none disabled:opacity-40"
          />
          <div className="flex items-center justify-between bg-neutral-50 px-4 py-2 border-t border-neutral-100">
            <div className="text-[10px] text-neutral-400">{"enter to send, shift+enter for new line"}</div>
            <button
              type="button"
              onClick={submit}
              disabled={disabled || sending || !input.trim()}
              className="small-caps bg-ember px-4 py-1.5 text-xs text-white hover:bg-ember-hover disabled:cursor-not-allowed disabled:opacity-40 transition-colors"
            >
              {sending ? "sending\u2026" : "send"}
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

type WidgetArtifact = {
  artifact: ChatArtifact;
  widget: Widget;
};

function widgetArtifactsFromArtifacts(artifacts: ChatArtifact[]): WidgetArtifact[] {
  return artifacts
    .map((artifact) => {
      const widget = artifact.inline_payload?.widget;
      return isWidget(widget) ? { artifact, widget } : null;
    })
    .filter((item): item is WidgetArtifact => item !== null);
}

function isWidget(value: unknown): value is Widget {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<Widget>;
  return (
    typeof candidate.kind === "string" &&
    typeof candidate.title === "string" &&
    Boolean(candidate.data) &&
    typeof candidate.data === "object"
  );
}

function ThinkingIndicator() {
  return (
    <div className="flex items-center gap-3 py-2">
      <ThinkingDots />
      <span className="text-sm text-neutral-400 animate-pulse">analyzing your data…</span>
    </div>
  );
}

function ThinkingDots() {
  return (
    <span className="flex shrink-0 items-center gap-1" aria-hidden="true">
      <span
        className="inline-block h-1.5 w-1.5 bg-ember animate-bounce"
        style={{ animationDelay: "0ms", animationDuration: "1s" }}
      />
      <span
        className="inline-block h-1.5 w-1.5 bg-ember animate-bounce"
        style={{ animationDelay: "150ms", animationDuration: "1s" }}
      />
      <span
        className="inline-block h-1.5 w-1.5 bg-ember animate-bounce"
        style={{ animationDelay: "300ms", animationDuration: "1s" }}
      />
    </span>
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
  return (
    <div className="mb-3">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="flex w-full items-center justify-between border border-neutral-200 bg-white px-3 py-2 text-left hover:border-orange-200 hover:bg-orange-50"
      >
        <span className="flex min-w-0 items-center gap-2">
          <ThinkingDots />
          <span className="small-caps text-[10px] text-neutral-500">
            thinking
          </span>
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
        </div>
      </div>
    </div>
  );
}

function WidgetGrid({
  widgets,
  idPrefix,
}: {
  widgets: Widget[];
  idPrefix: string;
}) {
  return (
    <div className="mb-4 mt-3 grid gap-3">
      {widgets.map((widget, index) => (
        <div
          key={`${idPrefix}:widget:${index}`}
          className="border border-neutral-200 bg-white px-4 py-3 shadow-[0_10px_30px_rgba(14,14,14,0.04)]"
        >
          <WidgetRenderer widget={widget} height={260} />
        </div>
      ))}
    </div>
  );
}

function fallbackMessages(turn: ChatTurn): ChatMessage[] {
  const messages: ChatMessage[] = [
    {
      id: `${turn.id}:user`,
      turn_id: turn.id,
      role: "user",
      content: turn.user_message,
      tool_name: null,
      tool_args: null,
      tool_result: null,
      created_at: turn.created_at,
    },
  ];
  if (turn.assistant_message) {
    messages.push({
      id: `${turn.id}:assistant`,
      turn_id: turn.id,
      role: "assistant",
      content: turn.assistant_message,
      tool_name: null,
      tool_args: null,
      tool_result: null,
      created_at: turn.created_at,
    });
  }
  return messages;
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="mb-4 flex justify-end">
      <div className="max-w-[78%] border border-orange-200 bg-orange-50 px-4 py-3 text-base text-ink shadow-[inset_0_1px_0_rgba(255,255,255,0.75)]">
        <MarkdownText text={text} compact />
      </div>
    </div>
  );
}

function AssistantBubble({ text }: { text: string }) {
  return (
    <div className="mb-4">
      <div className="small-caps mb-2 text-sm text-ember">cerno</div>
      <div className="max-w-3xl text-base text-ink">
        <MarkdownText text={text || "\u2026"} />
      </div>
    </div>
  );
}

function LiveChatBlock({ liveChat }: { liveChat: LiveChatState }) {
  const [traceOpen, setTraceOpen] = useState(false);
  const hasTrace = Boolean(liveChat.reasoningText.trim()) || liveChat.tools.length > 0;

  return (
    <div className="hairline border-b py-4">
      <UserBubble text={liveChat.userMessage} />
      <div className="small-caps mb-2 text-sm text-ember">cerno</div>
      {hasTrace ? (
        <LiveTraceDisclosure
          open={traceOpen}
          liveChat={liveChat}
          onToggle={() => setTraceOpen((open) => !open)}
        />
      ) : null}
      {liveChat.assistantText ? (
        <div className="max-w-3xl text-base text-ink">
          <MarkdownText text={liveChat.assistantText} />
        </div>
      ) : liveChat.error ? (
        <div className="border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          {liveChat.error}
        </div>
      ) : hasTrace ? null : (
        <ThinkingIndicator />
      )}
    </div>
  );
}

function LiveTraceDisclosure({
  open,
  liveChat,
  onToggle,
}: {
  open: boolean;
  liveChat: LiveChatState;
  onToggle: () => void;
}) {
  const stepCount =
    liveChat.tools.length + (liveChat.reasoningText.trim() ? 1 : 0);

  return (
    <div className="mb-3">
      <button
        type="button"
        aria-expanded={open}
        onClick={onToggle}
        className="flex w-full items-center justify-between border border-neutral-200 bg-white px-3 py-2 text-left hover:border-orange-200 hover:bg-orange-50"
      >
        <span className="flex min-w-0 items-center gap-2">
          <ThinkingDots />
          <span className="small-caps text-[10px] text-neutral-500">
            thinking
          </span>
        </span>
        <span className="flex items-center gap-2">
          <span className="font-mono text-[10px] text-neutral-400">
            {stepCount} {stepCount === 1 ? "step" : "steps"}
          </span>
          <span className="text-xs text-ember">{open ? "\u2191" : "\u2193"}</span>
        </span>
      </button>
      {open ? (
        <div className="mt-2 space-y-2">
          {liveChat.reasoningText.trim() ? (
            <div className="border border-neutral-200 bg-neutral-50 px-3 py-2 text-sm text-neutral-600">
              <div className="small-caps mb-1 text-xs text-neutral-500">thinking</div>
              <MarkdownText text={liveChat.reasoningText} compact />
            </div>
          ) : null}
          {liveChat.tools.map((tool) => (
            <div key={tool.callId} className="border border-orange-200 bg-white px-3 py-2">
              <div className="flex items-center justify-between gap-3">
                <span className="truncate font-mono text-xs text-ink">
                  {formatToolName(tool.name || null)}
                </span>
                <span className="small-caps text-xs text-neutral-400">
                  {tool.result ? "done" : "running"}
                </span>
              </div>
            </div>
          ))}
        </div>
      ) : null}
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

function traceItemsForAssistant(
  assistant: ChatMessage,
  messages: ChatMessage[],
  traceItems: TraceItem[],
): TraceItem[] {
  const assistantIndex = messages.findIndex((message) => message.id === assistant.id);
  if (assistantIndex < 0) return [];
  let start = 0;
  for (let i = assistantIndex - 1; i >= 0; i -= 1) {
    const message = messages[i];
    if (!message) continue;
    if (message.role === "user" || (message.role === "assistant" && !message.tool_name)) {
      start = i + 1;
      break;
    }
  }
  const traceIds = new Set(
    messages
      .slice(start, assistantIndex)
      .filter((message) => message.role === "assistant" && Boolean(message.tool_name))
      .map((message) => message.id),
  );
  return traceItems.filter((item) => traceIds.has(item.message.id));
}

function widgetArtifactsForMessage(
  assistant: ChatMessage,
  messages: ChatMessage[],
  widgetArtifacts: WidgetArtifact[],
): WidgetArtifact[] {
  const direct = widgetArtifacts.filter((item) => item.artifact.message_id === assistant.id);
  if (direct.length > 0) return direct;
  return widgetArtifacts.filter((item) => {
    if (item.artifact.message_id) return false;
    const owner = nearestAssistantBeforeArtifact(item.artifact, messages);
    return owner?.id === assistant.id;
  });
}

function nearestAssistantBeforeArtifact(
  artifact: ChatArtifact,
  messages: ChatMessage[],
): ChatMessage | null {
  const artifactTime = new Date(artifact.created_at).getTime();
  const assistantMessages = messages.filter(
    (message) => message.role === "assistant" && !message.tool_name,
  );
  if (!Number.isFinite(artifactTime)) {
    return assistantMessages[assistantMessages.length - 1] ?? null;
  }
  let owner: ChatMessage | null = null;
  for (const message of assistantMessages) {
    const messageTime = new Date(message.created_at).getTime();
    if (!Number.isFinite(messageTime)) continue;
    if (messageTime <= artifactTime) owner = message;
  }
  return owner ?? assistantMessages[assistantMessages.length - 1] ?? null;
}

function formatToolName(name: string | null) {
  if (!name) return "tool";
  return name.replace(/_/g, " ");
}

export function MarkdownText({
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
