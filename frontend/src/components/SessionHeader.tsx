import { useRef } from "react";
import type { Session } from "../lib/types";

type Props = {
  session: Session | null;
  onUpload: (file: File) => void;
  uploading: boolean;
};

export function SessionHeader({ session, onUpload, uploading }: Props) {
  const inputRef = useRef<HTMLInputElement | null>(null);

  const handlePick = () => inputRef.current?.click();

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) onUpload(file);
    e.target.value = "";
  };

  return (
    <div className="hairline flex items-center justify-between border-b px-6 py-3">
      <div className="flex items-center gap-3">
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
          className="hidden"
          onChange={handleChange}
        />
        <button
          type="button"
          onClick={handlePick}
          disabled={!session || uploading}
          className="small-caps border border-ink px-3 py-1 text-xs hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {uploading ? "uploading\u2026" : "+ upload file"}
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
