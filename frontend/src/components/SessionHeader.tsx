import { useRef } from "react";
import type { Session } from "../lib/types";

type Props = {
  session: Session | null;
  onUpload: (files: File[]) => void;
  uploading: boolean;
  onHome: () => void;
  onDelete: () => void;
};

export function SessionHeader({
  session,
  onUpload,
  uploading,
  onHome,
  onDelete,
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
    <div className="hairline flex items-center justify-between border-b px-6 py-3">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onHome}
          className="small-caps text-xs text-neutral-500 hover:text-ink"
          title="Back to home"
        >
          {"\u2190 home"}
        </button>
        <span className="text-neutral-300">|</span>
        <StatusDot status={session?.status ?? "new"} />
        <div className="small-caps text-sm">
          {session ? session.name : "no session"}
        </div>
      </div>
      <div className="flex items-center gap-2">
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
