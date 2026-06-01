import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, FileText, X } from "lucide-react";
import { getDocument } from "../lib/api";
import { formatNumber, type NumberSystem } from "../lib/format-number";
import type { DocumentPageDetail, DocumentRecord } from "../lib/types";

export type DocumentTarget = {
  documentId: string;
  pageNumber?: number;
};

type DocumentViewerProps = {
  sessionId: string;
  target: DocumentTarget;
  documents: DocumentRecord[];
  numberSystem: NumberSystem;
  onClose: () => void;
};

export function DocumentViewer({
  sessionId,
  target,
  documents,
  numberSystem,
  onClose,
}: DocumentViewerProps) {
  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const fallback = documents.find((document) => document.id === target.documentId) ?? null;
  const query = useQuery({
    queryKey: ["document", sessionId, target.documentId],
    queryFn: () => getDocument(sessionId, target.documentId),
    placeholderData: fallback
      ? {
          ...fallback,
          pages: fallback.pages.map((page) => ({
            ...page,
            markdown: "",
            review_image_url: null,
          })),
        }
      : undefined,
  });
  const document = query.data ?? null;
  const activePage = target.pageNumber ?? document?.pages[0]?.page_number ?? 1;
  const reviewCount = document?.pages.filter((page) => page.low_confidence).length ?? 0;

  useEffect(() => {
    const handle = window.setTimeout(() => {
      pageRefs.current[activePage]?.scrollIntoView({ block: "start", behavior: "smooth" });
    }, 50);
    return () => window.clearTimeout(handle);
  }, [activePage, document?.id]);

  if (!document) {
    return (
      <aside className="flex h-full flex-col bg-card">
        <ViewerHeader title="Document" subtitle="loading" onClose={onClose} />
      </aside>
    );
  }

  return (
    <aside className="flex h-full flex-col bg-card">
      <ViewerHeader
        title={document.filename}
        subtitle={`${formatNumber(document.page_count, numberSystem)} pages`}
        onClose={onClose}
      />
      <div className="border-b border-border px-4 py-3">
        <div className="grid grid-cols-3 gap-2">
          <ViewerStat label="status" value={document.status} />
          <ViewerStat label="pages" value={formatNumber(document.page_count, numberSystem)} />
          <ViewerStat label="review" value={reviewCount ? formatNumber(reviewCount, numberSystem) : "none"} />
        </div>
      </div>
      <div className="flex gap-1 overflow-x-auto border-b border-border px-4 py-2">
        {document.pages.map((page) => (
          <button
            key={page.id}
            type="button"
            onClick={() => {
              pageRefs.current[page.page_number]?.scrollIntoView({
                block: "start",
                behavior: "smooth",
              });
            }}
            className={`small-caps shrink-0 border px-2.5 py-1 text-[10px] transition ${
              page.page_number === activePage
                ? "border-primary bg-primary/10 text-primary"
                : "border-border text-muted-foreground hover:border-primary hover:text-primary"
            }`}
          >
            {formatNumber(page.page_number, numberSystem)}
            {page.low_confidence ? " review" : ""}
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
        {query.isError ? (
          <div className="border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            Document could not be loaded.
          </div>
        ) : null}
        <div className="grid gap-4">
          {document.pages.map((page) => (
            <DocumentPageCard
              key={page.id}
              page={page}
              numberSystem={numberSystem}
              refCallback={(node) => {
                pageRefs.current[page.page_number] = node;
              }}
            />
          ))}
        </div>
      </div>
    </aside>
  );
}

function ViewerHeader({
  title,
  subtitle,
  onClose,
}: {
  title: string;
  subtitle: string;
  onClose: () => void;
}) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-border px-4 py-4">
      <div className="min-w-0">
        <div className="flex items-center gap-2 text-primary">
          <FileText size={16} strokeWidth={1.8} />
          <span className="small-caps text-xs">document</span>
        </div>
        <h2 className="mt-2 truncate font-mono text-lg text-foreground" title={title}>
          {title}
        </h2>
        <div className="mt-1 text-xs text-muted-foreground">{subtitle}</div>
      </div>
      <button
        type="button"
        onClick={onClose}
        className="grid size-8 shrink-0 place-items-center border border-border text-muted-foreground transition hover:border-primary hover:text-primary"
        aria-label="Close document viewer"
        title="Close"
      >
        <X size={15} strokeWidth={1.8} />
      </button>
    </div>
  );
}

function ViewerStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-border bg-background px-2.5 py-2">
      <div className="small-caps text-[10px] text-muted-foreground">{label}</div>
      <div className="mt-1 truncate font-mono text-sm text-foreground">{value}</div>
    </div>
  );
}

function DocumentPageCard({
  page,
  numberSystem,
  refCallback,
}: {
  page: DocumentPageDetail;
  numberSystem: NumberSystem;
  refCallback: (node: HTMLDivElement | null) => void;
}) {
  const quality = Math.round(page.quality_score * 100);
  const hasMarkdown = page.markdown.trim().length > 0;

  return (
    <article ref={refCallback} className="scroll-mt-4 border border-border bg-background">
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-3 py-2">
        <div>
          <div className="small-caps text-[10px] text-muted-foreground">
            page {formatNumber(page.page_number, numberSystem)}
          </div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            {page.source.replace(/_/g, " ")} · {formatNumber(page.char_count, numberSystem)} chars
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="small-caps border border-border px-2 py-1 text-[10px] text-muted-foreground">
            {formatNumber(quality, numberSystem)} quality
          </span>
          {page.low_confidence ? <ReviewBadge /> : <ConfirmedBadge />}
        </div>
      </header>
      {page.low_confidence ? (
        <div className="border-b border-border bg-muted/60 px-3 py-2 text-xs leading-5 text-muted-foreground">
          <span className="font-medium text-foreground">Needs review.</span>{" "}
          {friendlyQualityReasons(page.quality_reasons)}
        </div>
      ) : null}
      <div className="grid gap-3 p-3">
        {page.review_image_url ? (
          <div className="border border-border bg-card p-2">
            <div className="small-caps mb-2 text-[10px] text-muted-foreground">
              source image for verification
            </div>
            <img
              src={page.review_image_url}
              alt={`Rendered page ${page.page_number} for OCR review`}
              className="max-h-[520px] w-full object-contain"
            />
          </div>
        ) : null}
        <div className="min-w-0 border border-border bg-card px-3 py-3">
          {hasMarkdown ? (
            <DocumentMarkdown markdown={page.markdown} />
          ) : (
            <div className="text-sm text-muted-foreground">
              Page text is still loading.
            </div>
          )}
        </div>
      </div>
    </article>
  );
}

function ReviewBadge() {
  return (
    <span className="inline-flex items-center gap-1 border border-destructive/40 bg-destructive/10 px-2 py-1 text-[10px] text-destructive">
      <AlertTriangle size={12} strokeWidth={1.8} />
      <span className="small-caps">needs review</span>
    </span>
  );
}

function ConfirmedBadge() {
  return (
    <span className="small-caps border border-primary/30 bg-primary/10 px-2 py-1 text-[10px] text-primary">
      confirmed
    </span>
  );
}

export function DocumentMarkdown({ markdown }: { markdown: string }) {
  const blocks = useMemo(() => parseBlocks(markdown), [markdown]);
  return (
    <div className="space-y-3 text-sm leading-6 text-foreground">
      {blocks.map((block, index) => {
        if (block.kind === "heading") {
          return (
            <h3 key={index} className="font-mono text-base text-foreground">
              {inline(block.lines[0] ?? "")}
            </h3>
          );
        }
        if (block.kind === "list") {
          return (
            <ul key={index} className="list-disc space-y-1 pl-5">
              {block.lines.map((line, lineIndex) => (
                <li key={lineIndex}>{inline(line)}</li>
              ))}
            </ul>
          );
        }
        if (block.kind === "ordered-list") {
          return (
            <ol key={index} className="list-decimal space-y-1 pl-5">
              {block.lines.map((line, lineIndex) => (
                <li key={lineIndex}>{inline(line)}</li>
              ))}
            </ol>
          );
        }
        if (block.kind === "table") {
          const table = parseTable(block.lines);
          return (
            <div key={index} className="overflow-x-auto border border-border">
              <table className="w-full border-collapse text-left text-xs">
                <thead className="bg-muted text-muted-foreground">
                  <tr>
                    {table.header.map((cell, cellIndex) => (
                      <th key={cellIndex} className="border-b border-border px-2 py-1 font-mono">
                        {inline(cell)}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {table.rows.map((row, rowIndex) => (
                    <tr key={rowIndex} className="border-t border-border/60">
                      {row.map((cell, cellIndex) => (
                        <td key={cellIndex} className="px-2 py-1 align-top">
                          {inline(cell)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        }
        return (
          <p key={index} className="whitespace-pre-wrap">
            {inline(block.lines.join("\n"))}
          </p>
        );
      })}
    </div>
  );
}

type Block = {
  kind: "paragraph" | "heading" | "list" | "ordered-list" | "table";
  lines: string[];
};

function parseBlocks(markdown: string): Block[] {
  const blocks: Block[] = [];
  const lines = markdown.replace(/\r\n/g, "\n").split("\n");
  let paragraph: string[] = [];

  const flush = () => {
    if (paragraph.length === 0) return;
    blocks.push({ kind: "paragraph", lines: paragraph });
    paragraph = [];
  };

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i] ?? "";
    if (!line.trim()) {
      flush();
      continue;
    }
    if (isTableStart(lines, i)) {
      flush();
      const tableLines: string[] = [];
      while (i < lines.length && lines[i]?.includes("|") && lines[i]?.trim()) {
        tableLines.push(lines[i] ?? "");
        i += 1;
      }
      i -= 1;
      blocks.push({ kind: "table", lines: tableLines });
      continue;
    }
    const heading = line.match(/^#{1,6}\s+(.+)$/);
    if (heading) {
      flush();
      blocks.push({ kind: "heading", lines: [heading[1] ?? ""] });
      continue;
    }
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    if (bullet) {
      flush();
      const previous = blocks[blocks.length - 1];
      if (previous?.kind === "list") previous.lines.push(bullet[1] ?? "");
      else blocks.push({ kind: "list", lines: [bullet[1] ?? ""] });
      continue;
    }
    const ordered = line.match(/^\s*\d+\.\s+(.+)$/);
    if (ordered) {
      flush();
      const previous = blocks[blocks.length - 1];
      if (previous?.kind === "ordered-list") previous.lines.push(ordered[1] ?? "");
      else blocks.push({ kind: "ordered-list", lines: [ordered[1] ?? ""] });
      continue;
    }
    paragraph.push(line);
  }

  flush();
  return blocks.length > 0 ? blocks : [{ kind: "paragraph", lines: [""] }];
}

function isTableStart(lines: string[], index: number) {
  const line = lines[index] ?? "";
  const next = lines[index + 1] ?? "";
  return line.includes("|") && isTableDivider(next);
}

function isTableDivider(line: string) {
  const cells = line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|");
  return cells.length > 1 && cells.every((cell) => /^:?-{3,}:?$/.test(cell.trim()));
}

function parseTable(lines: string[]) {
  const rows = lines
    .filter((line) => !isTableDivider(line))
    .map((line) =>
      line
        .trim()
        .replace(/^\|/, "")
        .replace(/\|$/, "")
        .split("|")
        .map((cell) => cell.trim()),
    );
  const header = rows[0] ?? [];
  return {
    header,
    rows: rows.slice(1).map((row) => header.map((_, index) => row[index] ?? "")),
  };
}

function inline(text: string): ReactNode {
  const parts = text.split(/(`[^`]+`|\*\*[^*]+\*\*|\[illegible\])/gi);
  return parts.map((part, index) => {
    if (/^\[illegible\]$/i.test(part)) {
      return (
        <span key={index} className="border border-destructive/30 bg-destructive/10 px-1 text-destructive">
          {part}
        </span>
      );
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return (
        <code key={index} className="bg-muted px-1 font-mono text-xs">
          {part.slice(1, -1)}
        </code>
      );
    }
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    return part;
  });
}

function friendlyQualityReasons(reasons: string[]) {
  if (reasons.length === 0) return "Cerno marked this OCR as uncertain.";
  const labels = reasons.slice(0, 3).map((reason) => reason.replace(/_/g, " "));
  return `Reasons: ${labels.join(", ")}.`;
}
