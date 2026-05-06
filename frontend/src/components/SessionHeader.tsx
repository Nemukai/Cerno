import { useRef } from "react";
import { CernoLockup } from "./Brand";
import type { Session } from "../lib/types";

type Props = {
  session: Session | null;
  sessions: Session[];
  onUpload: (files: File[]) => void;
  uploading: boolean;
  onHome: () => void;
  onDelete: () => void;
  onResume: (session: Session) => void;
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
          className="flex h-8 w-8 shrink-0 items-center justify-center border border-neutral-200 bg-white font-mono text-lg leading-none text-neutral-500 transition hover:border-ember hover:text-ember"
          title="Back to workspaces"
          aria-label="Back to workspaces"
        >
          ←
        </button>
        <button
          type="button"
          onClick={onHome}
          className="shrink-0 text-neutral-500 transition hover:text-ink"
          title="Back to workspaces"
        >
          <CernoLockup markClassName="h-5 w-5" wordmarkClassName="text-sm" />
        </button>
        <StatusDot status={session?.status ?? "new"} />
        <div className="small-caps min-w-0 truncate text-sm">
          {session ? session.name : "no session"}
        </div>
        {sessions.length > 1 ? (
          <select
            value={session?.id ?? ""}
            onChange={(event) => {
              const next = sessions.find((item) => item.id === event.target.value);
              if (next && next.id !== session?.id) onResume(next);
            }}
            className="small-caps max-w-44 border border-neutral-200 bg-white px-2 py-1 text-xs text-neutral-600 focus:outline-none focus:ring-1 focus:ring-ember"
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
      {showFileActions ? (
      <div className="flex shrink-0 items-center gap-2">
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
          onClick={handlePick}
          disabled={!session || uploading}
          className="small-caps rounded border border-neutral-300 px-3 py-1.5 text-xs text-neutral-600 hover:border-ember hover:text-ember transition-colors disabled:cursor-not-allowed disabled:opacity-40"
        >
          {uploading ? "uploading\u2026" : "+ upload files"}
        </button>
        <button
          type="button"
          onClick={handleDelete}
          disabled={!session}
          className="small-caps rounded border border-red-200 px-3 py-1.5 text-xs text-red-600 hover:bg-red-50 transition-colors disabled:cursor-not-allowed disabled:opacity-40"
          title="Delete this session"
        >
          delete
        </button>
      </div>
      ) : null}
    </div>
  );
}

function StatusDot({ status }: { status: string }) {
  const color =
    status === "ready"
      ? "bg-ember"
      : status === "analyzing" || status === "ingesting"
        ? "bg-neutral-500"
        : "bg-neutral-300";
  return <span className={`inline-block h-2.5 w-2.5 ${color}`} />;
}
