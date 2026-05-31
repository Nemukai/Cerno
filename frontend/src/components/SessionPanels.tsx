import { useMemo, useRef, useState } from "react";
import type {
  ChatMessage,
  ChatTurn,
  DiscoveryStatus,
  FileRecord,
  Session,
} from "../lib/types";

type WorkspaceSidebarProps = {
  session: Session;
  files: FileRecord[];
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
            {files.length}
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
  discoveryStatus: DiscoveryStatus;
  uploading: boolean;
  processing: boolean;
  onUpload: (files: File[]) => void;
  onDeleteFile: (fileId: string) => void;
  onProcess: () => void;
};

export function FilesPanel({
  files,
  discoveryStatus,
  uploading,
  processing,
  onUpload,
  onDeleteFile,
  onProcess,
}: FilesPanelProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const totalRows = files.reduce((sum, file) => sum + file.row_count, 0);

  const pickFiles = () => inputRef.current?.click();
  const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(event.target.files ?? []);
    if (selected.length > 0) onUpload(selected);
    event.target.value = "";
  };

  if (files.length === 0) {
    return (
      <section className="flex min-h-full items-center justify-center px-8 py-10">
        <input
          ref={inputRef}
          type="file"
          accept=".csv,.xlsx,.xls"
          multiple
          className="hidden"
          onChange={handleChange}
        />
        <button
          type="button"
          onClick={pickFiles}
          disabled={uploading}
          className="w-full max-w-xl border border-dashed border-border bg-card px-8 py-16 text-center transition hover:border-primary disabled:cursor-not-allowed disabled:opacity-50"
        >
          <span className="small-caps text-sm text-primary">new workspace</span>
          <span className="mt-3 block font-mono text-3xl text-foreground">
            Upload files to start.
          </span>
          <span className="mt-3 block text-base leading-7 text-muted-foreground">
            Add Excel or CSV files. After upload, Cerno will show the process
            action before generating insights.
          </span>
          <span className="small-caps mt-6 inline-block bg-primary px-4 py-3 text-sm text-primary-foreground">
            {uploading ? "uploading..." : "choose files"}
          </span>
        </button>
      </section>
    );
  }

  return (
    <section className="px-8 py-7">
      <input
        ref={inputRef}
        type="file"
        accept=".csv,.xlsx,.xls"
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
        <FileStat label="files" value={formatCompactNumber(files.length)} />
        <FileStat label="total rows" value={formatCompactNumber(totalRows)} />
        <FileStat label="status" value={statusLabel(discoveryStatus, processing)} />
        <FileStat
          label="last file"
          value={files[0] ? formatDate(files[files.length - 1]?.created_at) : "none"}
        />
      </div>

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
                <FileFact label="rows" value={formatCompactNumber(file.row_count)} />
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

function formatCompactNumber(value: number): string {
  if (value < 1_000) return value.toLocaleString();
  if (value < 1_000_000) {
    const compact = value / 1_000;
    return `${compact >= 10 ? compact.toFixed(0) : compact.toFixed(1)}K`;
  }
  const compact = value / 1_000_000;
  return `${compact >= 10 ? compact.toFixed(0) : compact.toFixed(1)}M`;
}

function formatDate(value: string | undefined): string {
  if (!value) return "none";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "unknown";
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
