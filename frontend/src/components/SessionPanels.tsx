import { useMemo, useRef, useState } from "react";
import { AlertTriangle, FileText } from "lucide-react";
import { formatNumber, type NumberSystem } from "../lib/format-number";
import type {
  ChatMessage,
  ChatTurn,
  DocumentRecord,
  DiscoveryStatus,
  FileRecord,
  ProcessingEvent,
  Session,
} from "../lib/types";
import type { DocumentTarget } from "./DocumentViewer";

type WorkspaceSidebarProps = {
  session: Session;
  files: FileRecord[];
  documents: DocumentRecord[];
  numberSystem: NumberSystem;
  turns: ChatTurn[];
  messagesByTurn: Record<string, ChatMessage[]>;
  activeTurnId: string | null;
  onSelectTab: (tab: "ask" | "insights") => void;
  onSelectTurn: (turnId: string) => void;
  onNewChat: () => void;
  onRenameTurn: (turnId: string, title: string) => void;
  onDeleteTurn: (turnId: string) => void;
};

export function WorkspaceSidebar({
  session,
  files,
  documents,
  numberSystem,
  turns,
  messagesByTurn = {},
  activeTurnId,
  onSelectTab,
  onSelectTurn,
  onNewChat,
  onRenameTurn,
  onDeleteTurn,
}: WorkspaceSidebarProps) {
  const [menu, setMenu] = useState<{
    turn: ChatTurn;
    x: number;
    y: number;
  } | null>(null);
  const orderedTurns = useMemo(
    () =>
      [...turns].sort(
        (a, b) =>
          latestTurnActivity(b, messagesByTurn).getTime() -
          latestTurnActivity(a, messagesByTurn).getTime(),
      ),
    [messagesByTurn, turns],
  );

  const renameTurn = (turn: ChatTurn) => {
    const current = defaultTurnTitle(turn);
    const next = window.prompt("Rename chat", current);
    if (next === null) return;
    const trimmed = next.trim();
    if (!trimmed) return;
    onRenameTurn(turn.id, trimmed);
    setMenu(null);
  };

  const deleteTurn = (turn: ChatTurn) => {
    const ok = window.confirm(`Delete "${defaultTurnTitle(turn)}"?`);
    if (!ok) return;
    setMenu(null);
    onDeleteTurn(turn.id);
  };

  return (
    <aside className="relative flex h-full flex-col" onClick={() => setMenu(null)}>
      <div className="border-border border-b px-5 py-4">
        <div className="min-w-0">
          <div className="small-caps text-sm text-muted-foreground/70">workspace</div>
          <div className="mt-1 truncate font-mono text-lg text-foreground">{session.name}</div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <button
          type="button"
          onClick={() => onSelectTab("insights")}
          className="flex w-full items-center justify-between bg-muted px-3 py-2 text-left transition hover:bg-muted"
          title="Open file summaries in Insights"
        >
          <span className="text-sm font-medium text-foreground">Files</span>
          <span className="font-mono text-sm text-muted-foreground">
            {formatNumber(files.length, numberSystem)}
          </span>
        </button>
        <button
          type="button"
          onClick={() => onSelectTab("insights")}
          className="mt-2 flex w-full items-center justify-between bg-muted/70 px-3 py-2 text-left transition hover:bg-muted"
          title="Open ingested documents in Insights"
        >
          <span className="text-sm font-medium text-foreground">Documents</span>
          <span className="font-mono text-sm text-muted-foreground">
            {formatNumber(documents.length, numberSystem)}
          </span>
        </button>

        <div className="mt-6 flex items-center justify-between gap-3 px-1">
          <div className="small-caps text-xs text-muted-foreground">chats</div>
          <button
            type="button"
            onClick={onNewChat}
            className="text-xs text-muted-foreground hover:text-primary transition"
          >
            + new
          </button>
        </div>
        <div className="mt-2 grid gap-1">
          {orderedTurns.length > 0 ? (
            orderedTurns.slice(0, 24).map((turn) => (
              <button
                key={turn.id}
                type="button"
                onClick={() => onSelectTurn(turn.id)}
                onContextMenu={(event) => {
                  event.preventDefault();
                  setMenu({ turn, x: event.clientX, y: event.clientY });
                }}
                className={`min-w-0 px-3 py-2 text-left transition ${
                  activeTurnId === turn.id
                    ? "bg-primary/10 text-primary"
                    : "hover:bg-primary/10"
                }`}
              >
                <div className="truncate text-sm font-medium text-foreground">
                  {defaultTurnTitle(turn)}
                </div>
                <div className="mt-0.5 flex items-center justify-between gap-2">
                  <span className="text-[10px] uppercase tracking-wider text-muted-foreground/70">
                    {turn.state}
                  </span>
                  <span className="text-[10px] text-muted-foreground/70">
                    {formatRelativeActivity(latestTurnActivity(turn, messagesByTurn))}
                  </span>
                </div>
              </button>
            ))
          ) : (
            <div className="px-3 py-3 text-sm text-muted-foreground">
              No chats yet.
            </div>
          )}
        </div>
        {menu ? (
          <div
            className="fixed z-50 w-36 border border-border bg-card p-1 shadow-lg"
            style={{ left: menu.x, top: menu.y }}
            onClick={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => renameTurn(menu.turn)}
              className="block w-full px-2 py-1.5 text-left text-sm text-foreground hover:bg-muted"
            >
              Edit name
            </button>
            <button
              type="button"
              onClick={() => deleteTurn(menu.turn)}
              className="block w-full px-2 py-1.5 text-left text-sm text-destructive hover:bg-destructive/10"
            >
              Delete
            </button>
          </div>
        ) : null}
      </div>
    </aside>
  );
}

type FilesPanelProps = {
  files: FileRecord[];
  documents: DocumentRecord[];
  discoveryStatus: DiscoveryStatus;
  uploading: boolean;
  processing: boolean;
  events: ProcessingEvent[];
  numberSystem: NumberSystem;
  onUpload: (files: File[]) => void;
  onDeleteFile: (fileId: string) => void;
  onProcess: () => void;
  onOpenDocument: (target: DocumentTarget) => void;
};

export function FilesPanel({
  files,
  documents,
  discoveryStatus,
  uploading,
  processing,
  events,
  numberSystem,
  onUpload,
  onDeleteFile,
  onProcess,
  onOpenDocument,
}: FilesPanelProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const documentInputRef = useRef<HTMLInputElement | null>(null);
  const totalRows = files.reduce((sum, file) => sum + file.row_count, 0);
  const reviewPages = documents.reduce(
    (sum, document) => sum + document.pages.filter((page) => page.low_confidence).length,
    0,
  );

  const pickFiles = () => inputRef.current?.click();
  const pickDocuments = () => documentInputRef.current?.click();
  const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(event.target.files ?? []);
    if (selected.length > 0) onUpload(selected);
    event.target.value = "";
  };

  if (files.length === 0 && documents.length === 0) {
    return (
      <section className="flex min-h-full items-center justify-center px-8 py-10">
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.xlsx,.xls,.pdf,.txt,.md,.markdown"
          multiple
          className="hidden"
          onChange={handleChange}
        />
        <div className="w-full max-w-xl">
          <button
            type="button"
            onClick={pickFiles}
            disabled={uploading}
            className="w-full border border-dashed border-border bg-card px-8 py-16 text-center transition hover:border-primary disabled:cursor-not-allowed disabled:opacity-50"
          >
            <span className="small-caps text-sm text-primary">new workspace</span>
            <span className="mt-3 block font-mono text-3xl text-foreground">
              Upload files or regulations to start.
            </span>
            <span className="mt-3 block text-base leading-7 text-muted-foreground">
              Add spreadsheets for analysis, or PDFs and text documents for
              citation-backed regulatory checks.
            </span>
            <span className="small-caps mt-6 inline-block bg-primary px-4 py-3 text-sm text-primary-foreground">
              {uploading ? "uploading..." : "choose files"}
            </span>
          </button>
          <div className="mt-3">
            <DocumentProcessingStrip
              events={events}
              uploading={uploading}
              numberSystem={numberSystem}
            />
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="px-8 py-7">
      <input
        ref={inputRef}
        type="file"
        accept=".csv,.xlsx,.xls,.pdf,.txt,.md,.markdown"
        multiple
        className="hidden"
        onChange={handleChange}
      />
      <input
        ref={documentInputRef}
        type="file"
        accept=".pdf,.txt,.md,.markdown"
        multiple
        className="hidden"
        onChange={handleChange}
      />
      <div className="flex flex-wrap items-center justify-between gap-5 border-b border-border pb-4">
        <div>
          <h2 className="font-mono text-2xl text-foreground">
            Data Files
          </h2>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={pickDocuments}
            disabled={uploading || processing}
            className="small-caps border border-border px-4 py-3 text-sm text-foreground hover:border-primary hover:text-primary disabled:opacity-40"
          >
            add documents
          </button>
          <button
            type="button"
            onClick={pickFiles}
            disabled={uploading || processing}
            className="small-caps bg-primary px-4 py-3 text-sm text-primary-foreground hover:bg-primary/90 disabled:opacity-40"
          >
            {uploading ? "uploading..." : "add files"}
          </button>
          <button
            type="button"
            onClick={onProcess}
            disabled={processing || files.length === 0}
            className="small-caps border border-foreground px-4 py-3 text-sm hover:bg-muted disabled:opacity-40"
          >
            {processing
              ? "processing..."
              : discoveryStatus === "empty"
                ? "process files"
                : "rerun processing"}
          </button>
        </div>
      </div>

      <div className="mt-6 grid gap-4 sm:grid-cols-4">
        <FileStat label="files" value={formatNumber(files.length, numberSystem)} />
        <FileStat label="total rows" value={formatNumber(totalRows, numberSystem)} />
        <FileStat label="documents" value={formatNumber(documents.length, numberSystem)} />
        <FileStat label="review pages" value={formatNumber(reviewPages, numberSystem)} />
        <FileStat label="status" value={statusLabel(discoveryStatus, processing)} />
      </div>

      <DocumentLibrary
        documents={documents}
        numberSystem={numberSystem}
        uploading={uploading}
        events={events}
        onUploadDocuments={pickDocuments}
        onOpenDocument={onOpenDocument}
      />

      {files.length > 0 ? (
        <div className="mt-7 grid gap-4">
          {files.map((file) => (
            <article key={file.id} className="border border-border bg-card px-5 py-4">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <h3 className="truncate font-mono text-xl text-foreground">
                    {file.friendly_name || file.filename}
                  </h3>
                  <p className="mt-1 max-w-3xl text-sm leading-6 text-muted-foreground">
                    {file.description || file.filename}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    if (window.confirm(`Remove ${file.filename}? Cerno will refresh the workspace understanding after deletion.`)) {
                      onDeleteFile(file.id);
                    }
                  }}
                  disabled={processing}
                  className="small-caps border border-destructive/40 px-3 py-2 text-sm text-destructive hover:bg-destructive/10 disabled:opacity-40"
                >
                  delete
                </button>
              </div>
              <div className="mt-4 grid gap-3 text-sm text-muted-foreground sm:grid-cols-4">
                <FileFact label="rows" value={formatNumber(file.row_count, numberSystem)} />
                <FileFact label="schema" value={`v${file.schema_version}`} />
                <FileFact label="header" value={file.header_row === null ? "pending" : `row ${file.header_row + 1}`} />
                <FileFact label="uploaded" value={formatDate(file.created_at)} />
              </div>
            </article>
          ))}
        </div>
      ) : (
        <button
          type="button"
          onClick={pickFiles}
          className="mt-7 block w-full border border-dashed border-border bg-card/70 px-5 py-16 text-center text-base text-muted-foreground hover:border-foreground"
        >
          Drop files anywhere in the window or click to choose spreadsheets.
        </button>
      )}
    </section>
  );
}

export function DocumentLibrary({
  documents,
  numberSystem,
  uploading,
  events = [],
  onUploadDocuments,
  onOpenDocument,
}: {
  documents: DocumentRecord[];
  numberSystem: NumberSystem;
  uploading: boolean;
  events?: ProcessingEvent[];
  onUploadDocuments: () => void;
  onOpenDocument: (target: DocumentTarget) => void;
}) {
  const reviewPages = documents.reduce(
    (sum, document) => sum + document.pages.filter((page) => page.low_confidence).length,
    0,
  );

  return (
    <section className="mt-7 border border-border bg-card">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-5 py-4">
        <div>
          <div className="small-caps text-xs text-primary">documents / regulations</div>
          <h3 className="mt-1 font-mono text-xl text-foreground">Reference documents</h3>
        </div>
        <button
          type="button"
          onClick={onUploadDocuments}
          disabled={uploading}
          className="small-caps border border-border px-3 py-2 text-sm text-foreground transition hover:border-primary hover:text-primary disabled:opacity-40"
        >
          {uploading ? "uploading..." : "upload pdf / text"}
        </button>
      </div>
      <div className="grid gap-3 px-5 py-4">
        <DocumentProcessingStrip
          events={events}
          uploading={uploading}
          numberSystem={numberSystem}
        />
        {documents.length === 0 ? (
          <button
            type="button"
            onClick={onUploadDocuments}
            disabled={uploading}
            className="flex w-full items-center justify-between border border-dashed border-border bg-background px-4 py-5 text-left transition hover:border-primary disabled:opacity-40"
          >
            <span>
              <span className="block font-mono text-base text-foreground">
                Add PDFs, text files, or markdown regulations.
              </span>
              <span className="mt-1 block text-sm text-muted-foreground">
                Cerno will index them for cited chat answers.
              </span>
            </span>
            <FileText className="shrink-0 text-muted-foreground" size={20} strokeWidth={1.8} />
          </button>
        ) : (
          documents.map((document) => {
            const pagesNeedingReview = document.pages.filter((page) => page.low_confidence);
            const averageQuality =
              document.pages.length > 0
                ? document.pages.reduce((sum, page) => sum + page.quality_score, 0) /
                  document.pages.length
                : 0;
            return (
              <button
                key={document.id}
                type="button"
                onClick={() => onOpenDocument({ documentId: document.id })}
                className="grid gap-3 border border-border bg-background px-4 py-3 text-left transition hover:border-primary hover:bg-primary/5 md:grid-cols-[1fr_auto]"
              >
                <span className="min-w-0">
                  <span className="flex items-center gap-2">
                    <FileText size={16} strokeWidth={1.8} className="shrink-0 text-primary" />
                    <span className="truncate font-mono text-base text-foreground">
                      {document.filename}
                    </span>
                  </span>
                  <span className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
                    <span>{formatNumber(document.page_count, numberSystem)} pages</span>
                    <span>{document.status}</span>
                    <span>{formatNumber(Math.round(averageQuality * 100), numberSystem)} quality</span>
                  </span>
                </span>
                <span className="flex items-center gap-2">
                  {pagesNeedingReview.length > 0 ? (
                    <span className="inline-flex items-center gap-1 border border-destructive/40 bg-destructive/10 px-2 py-1 text-xs text-destructive">
                      <AlertTriangle size={13} strokeWidth={1.8} />
                      {formatNumber(pagesNeedingReview.length, numberSystem)} pages need review
                    </span>
                  ) : (
                    <span className="small-caps border border-primary/30 bg-primary/10 px-2 py-1 text-xs text-primary">
                      confirmed
                    </span>
                  )}
                </span>
              </button>
            );
          })
        )}
      </div>
      {reviewPages > 0 ? (
        <div className="border-t border-border px-5 py-3 text-sm text-muted-foreground">
          {formatNumber(reviewPages, numberSystem)} page{reviewPages === 1 ? "" : "s"} need human review before relying on OCR text.
        </div>
      ) : null}
    </section>
  );
}

function DocumentProcessingStrip({
  events,
  uploading,
  numberSystem,
}: {
  events: ProcessingEvent[];
  uploading: boolean;
  numberSystem: NumberSystem;
}) {
  const event = latestDocumentEvent(events);
  if (!event && !uploading) return null;

  const message = event?.message ?? "Uploading workspace item";
  const progress = event?.progress;
  return (
    <div className="border border-border bg-background px-4 py-3">
      <div className="flex items-center justify-between gap-3">
        <div>
          <div className="small-caps text-[10px] text-primary">
            {event ? (event.kind === "done" ? "document ready" : "document ingest") : "uploading"}
          </div>
          <div className="mt-1 text-sm text-foreground">{message}</div>
        </div>
        {typeof progress === "number" ? (
          <div className="font-mono text-xs text-muted-foreground">
            {formatNumber(progress, numberSystem)}%
          </div>
        ) : null}
      </div>
      {typeof progress === "number" ? (
        <div className="mt-3 h-1.5 overflow-hidden bg-muted">
          <div
            className="h-full bg-primary transition-all duration-700 ease-out"
            style={{ width: `${Math.max(0, Math.min(100, progress))}%` }}
          />
        </div>
      ) : null}
    </div>
  );
}

function latestDocumentEvent(events: ProcessingEvent[]): ProcessingEvent | null {
  const documentKinds = new Set(["reading_document", "ocr_page", "quality_review"]);
  return (
    [...events]
      .reverse()
      .find((event) => {
        if (documentKinds.has(event.kind)) return true;
        if (event.step_key === "embedding_chunks") return true;
        return event.message.toLowerCase().includes("document");
      }) ?? null
  );
}

function FileStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-border bg-card px-4 py-3">
      <div className="small-caps text-sm text-muted-foreground">{label}</div>
      <div className="mt-2 font-mono text-2xl text-foreground">{value}</div>
    </div>
  );
}

function FileFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="small-caps text-sm text-muted-foreground/70">{label}</div>
      <div className="mt-1 text-base text-foreground">{value}</div>
    </div>
  );
}

function statusLabel(status: DiscoveryStatus, processing: boolean) {
  if (processing || status === "discovering") return "processing";
  if (status === "pending_review") return "needs approval";
  if (status === "approved") return "approved";
  if (status === "failed") return "failed";
  if (status === "empty") return "not processed";
  return status;
}

function defaultTurnTitle(turn: ChatTurn): string {
  const trimmed = (turn.title || turn.user_message).trim();
  return trimmed.length > 52 ? `${trimmed.slice(0, 49)}...` : trimmed || "Untitled chat";
}

function latestTurnActivity(
  turn: ChatTurn,
  messagesByTurn: Record<string, ChatMessage[]>,
): Date {
  const messageDates = (messagesByTurn[turn.id] ?? [])
    .map((message) => new Date(message.created_at).getTime())
    .filter(Number.isFinite);
  const fallback = new Date(turn.created_at).getTime();
  const latest = Math.max(
    Number.isFinite(fallback) ? fallback : 0,
    ...messageDates,
  );
  return new Date(latest);
}

function formatRelativeActivity(date: Date): string {
  const diffMs = Date.now() - date.getTime();
  if (!Number.isFinite(diffMs)) return "unknown";
  const futureSafe = Math.max(0, diffMs);
  const minute = 60 * 1000;
  const hour = 60 * minute;
  const day = 24 * hour;
  if (futureSafe < minute) return "now";
  if (futureSafe < hour) return `${Math.floor(futureSafe / minute)}min`;
  if (futureSafe < day) return `${Math.floor(futureSafe / hour)}hr`;
  return `${Math.floor(futureSafe / day)}d`;
}

function formatDate(value: string | undefined): string {
  if (!value) return "none";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "unknown";
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
