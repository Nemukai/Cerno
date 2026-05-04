import { useRef } from "react";
import type { Session } from "../lib/types";

type Props = {
  session: Session | null;
  sessions: Session[];
  onUpload: (files: File[]) => void;
  uploading: boolean;
  onHome: () => void;
  onDelete: () => void;
  onResume: (session: Session) => void;
};

export function SessionHeader({
  session,
  sessions,
  onUpload,
  uploading,
  onHome,
  onDelete,
  onResume,
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
      `Delete session "${session.name}"? Files, links, dashboards, and chat history will be removed. This cannot be undone.`,
    );
    if (ok) onDelete();
  };

  return (
    <div className="flex h-full items-center justify-between gap-4 px-5">
      <div className="flex min-w-0 items-center gap-3">
        <button
          type="button"
          onClick={onHome}
          className="small-caps shrink-0 text-xs text-neutral-500 hover:text-ink"
          title="Back to home"
        >
          {"\u2190"}
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
          className="small-caps border border-ink px-3 py-1 text-xs hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {uploading ? "uploading\u2026" : "+ upload files"}
        </button>
        <button
          type="button"
          onClick={handleDelete}
          disabled={!session}
          className="small-caps border border-red-600 px-3 py-1 text-xs text-red-600 hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-40"
          title="Delete this session"
        >
          delete
        </button>
      </div>
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
