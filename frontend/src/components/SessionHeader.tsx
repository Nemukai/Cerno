import { useRef } from "react";
import { ThemeToggle } from "./Theme";
import type { Session } from "../lib/types";

function Sigil({ className = "h-5 w-5" }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} fill="none" aria-hidden="true">
      <rect x="1" y="1" width="30" height="30" stroke="currentColor" strokeOpacity="0.35" />
      <circle cx="16" cy="16" r="9.5" stroke="currentColor" strokeOpacity="0.55" />
      <circle cx="16" cy="16" r="3.4" fill="currentColor" />
      <path d="M16 1.5v5M16 25.5v5M1.5 16h5M25.5 16h5" stroke="currentColor" strokeOpacity="0.5" />
    </svg>
  );
}

type Props = {
  session: Session | null;
  sessions: Session[];
  onUpload: (files: File[]) => void;
  uploading: boolean;
  onHome: () => void;
  onDelete: () => void;
  onResume: (session: Session) => void;
  onPrefetch?: (sessionId: string) => void;
  showFileActions?: boolean;
};

export function SessionHeader({
  session,
  sessions,
  onUpload,
  uploading,
  onHome,
  onDelete,
  onResume,
  onPrefetch,
  showFileActions = true,
}: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null);

  const handlePick = () => inputRef.current?.click();

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const fs = Array.from(e.target.files ?? []);
    if (fs.length > 0) onUpload(fs);
    e.target.value = "";
  };

  const handleDelete = () => {
    if (!session) return;
    const ok = window.confirm(
      `Delete session "${session.name}"? Files, links, insights, and chat history will be removed. This cannot be undone.`,
    );
    if (ok) onDelete();
  };

  return (
    <div className="flex h-full items-center justify-between gap-4 px-5">
      <div className="flex min-w-0 items-center gap-3">
        <button
          type="button"
          onClick={onHome}
          className="flex h-8 w-8 shrink-0 items-center justify-center border border-border bg-card font-mono text-lg leading-none text-muted-foreground transition hover:border-primary hover:text-primary"
          title="Back to workspaces"
          aria-label="Back to workspaces"
        >
          ←
        </button>
        <button
          type="button"
          onClick={onHome}
          className="flex shrink-0 items-center gap-2 text-primary transition-opacity hover:opacity-80"
          title="Back to workspaces"
        >
          <Sigil className="h-5 w-5" />
          <span className="font-display text-sm font-medium tracking-[0.2em] text-foreground">
            CERNO
          </span>
        </button>
        <StatusDot status={session?.status ?? "new"} />
        <div className="small-caps min-w-0 truncate text-sm">
          {session ? session.name : "no session"}
        </div>
        {sessions.length > 1 ? (
          <select
            value={session?.id ?? ""}
            onFocus={() => {
              sessions.forEach((item) => onPrefetch?.(item.id));
            }}
            onMouseEnter={() => {
              sessions.forEach((item) => onPrefetch?.(item.id));
            }}
            onChange={(event) => {
              const next = sessions.find((item) => item.id === event.target.value);
              if (next && next.id !== session?.id) onResume(next);
            }}
            className="small-caps max-w-44 border border-border bg-card px-2 py-1 text-xs text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            title="Switch session"
          >
            {sessions.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {showFileActions ? (
          <>
            <input
              ref={inputRef}
              type="file"
              accept=".csv,.xlsx,.xls,.pdf,.txt,.md,.markdown"
              multiple
              className="hidden"
              onChange={handleChange}
            />
            <button
              type="button"
              onClick={handlePick}
              disabled={!session || uploading}
              className="small-caps rounded border border-border px-3 py-1.5 text-xs text-muted-foreground hover:border-primary hover:text-primary transition-colors disabled:cursor-not-allowed disabled:opacity-40"
            >
              {uploading ? "uploading\u2026" : "+ upload files"}
            </button>
            <button
              type="button"
              onClick={handleDelete}
              disabled={!session}
              className="small-caps rounded border border-destructive/40 px-3 py-1.5 text-xs text-destructive hover:bg-destructive/10 transition-colors disabled:cursor-not-allowed disabled:opacity-40"
              title="Delete this session"
            >
              delete
            </button>
          </>
        ) : null}
        <ThemeToggle />
      </div>
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === "ready"
      ? "bg-primary"
      : status === "analyzing" || status === "ingesting"
        ? "bg-muted-foreground"
        : "bg-border";
  return <span className={`inline-block h-2.5 w-2.5 ${color}`} />;
}
