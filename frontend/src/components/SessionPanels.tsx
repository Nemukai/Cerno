import { useMemo, useRef, useState } from "react";
import type {
  ChatTurn,
  DataDoc,
  DiscoveryResponse,
  DiscoveryStatus,
  FileRecord,
  Link,
  ProcessingEvent,
  Session,
} from "../lib/types";
import { MarkdownText } from "./ChatSidebar";

type WorkspaceSidebarProps = {
  session: Session;
  files: FileRecord[];
  turns: ChatTurn[];
  activeTurnId: string | null;
  onSelectTab: (tab: "ask" | "insights" | "files") => void;
  onSelectTurn: (turnId: string) => void;
  onNewChat: () => void;
  onRenameTurn: (turnId: string, title: string) => void;
  onDeleteTurn: (turnId: string) => void;
};

export function WorkspaceSidebar({
  session,
  files,
  turns,
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
      <div className="hairline border-b px-5 py-4">
        <div className="min-w-0">
          <div className="small-caps text-sm text-neutral-400">workspace</div>
          <div className="mt-1 truncate font-mono text-lg text-ink">{session.name}</div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        <button
          type="button"
          onClick={() => onSelectTab("files")}
          className="flex w-full items-center justify-between bg-neutral-50 px-3 py-2 text-left transition hover:bg-neutral-100"
        >
          <span className="text-sm font-medium text-ink">Files</span>
          <span className="font-mono text-sm text-neutral-500">
            {files.length}
          </span>
        </button>

        <div className="mt-6 flex items-center justify-between gap-3 px-1">
          <div className="small-caps text-xs text-neutral-500">chats</div>
          <button
            type="button"
            onClick={onNewChat}
            className="text-xs text-neutral-500 hover:text-ember transition"
          >
            + new
          </button>
        </div>
        <div className="mt-2 grid gap-1">
          {turns.length > 0 ? (
            turns.slice(0, 24).map((turn) => (
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
                    ? "bg-orange-50 text-ember"
                    : "hover:bg-orange-50"
                }`}
              >
                <div className="truncate text-sm font-medium text-ink">
                  {defaultTurnTitle(turn)}
                </div>
                <div className="mt-0.5 flex items-center justify-between gap-2">
                  <span className="text-[10px] uppercase tracking-wider text-neutral-400">
                    {turn.state}
                  </span>
                  <span className="text-[10px] text-neutral-400">
                    {formatDate(turn.created_at)}
                  </span>
                </div>
              </button>
            ))
          ) : (
            <div className="px-3 py-3 text-sm text-neutral-500">
              No chats yet.
            </div>
          )}
        </div>
        {menu ? (
          <div
            className="fixed z-50 w-36 border border-neutral-200 bg-white p-1 shadow-lg"
            style={{ left: menu.x, top: menu.y }}
            onClick={(event) => event.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => renameTurn(menu.turn)}
              className="block w-full px-2 py-1.5 text-left text-sm text-ink hover:bg-neutral-50"
            >
              Edit name
            </button>
            <button
              type="button"
              onClick={() => deleteTurn(menu.turn)}
              className="block w-full px-2 py-1.5 text-left text-sm text-red-600 hover:bg-red-50"
            >
              Delete
            </button>
          </div>
        ) : null}
      </div>
    </aside>
  );
}

type InsightsPanelProps = {
  files: FileRecord[];
  links: Link[];
  discovery: DiscoveryResponse | null;
  dataDoc: DataDoc | null;
  events: ProcessingEvent[];
  processing: boolean;
  approving: boolean;
  onProcess: () => void;
  onApprove: (
    files: DiscoveryResponse["files"],
    links: DiscoveryResponse["links"],
    overview: string,
  ) => void;
  onOpenFiles: () => void;
};

export function InsightsPanel({
  files,
  links,
  discovery,
  dataDoc,
  events,
  processing,
  approving,
  onProcess,
  onApprove,
  onOpenFiles,
}: InsightsPanelProps) {
  const status = discovery?.status ?? "empty";
  const isProcessingState = processing || status === "discovering";
  const insightsReady = status === "pending_review" || status === "approved";
  const visibleDataDoc = insightsReady ? dataDoc : null;
  const canApprove = discovery?.status === "pending_review";
  const overview = visibleDataDoc?.overview || (insightsReady ? discovery?.overview : "");
  const relationshipCards = useMemo(() => {
    if (!insightsReady) return [];
    if (visibleDataDoc?.relationships.length) {
      return visibleDataDoc.relationships.map((rel) => ({
        leftFileId: rel.left_file_id,
        leftColumn: rel.left_column,
        rightFileId: rel.right_file_id,
        rightColumn: rel.right_column,
        explanation: rel.explanation,
      }));
    }
    if (discovery?.links.length) {
      return discovery.links.map((link) => ({
        leftFileId: link.file_a_id,
        leftColumn: link.col_a,
        rightFileId: link.file_b_id,
        rightColumn: link.col_b,
        explanation: link.summary,
      }));
    }
    return links.map((link) => ({
      leftFileId: link.file_a,
      leftColumn: link.col_a,
      rightFileId: link.file_b,
      rightColumn: link.col_b,
      explanation: link.summary ?? "",
    }));
  }, [discovery, insightsReady, links, visibleDataDoc]);

  return (
    <section className="px-8 py-7">
      <div className="flex flex-wrap items-center justify-between gap-5 border-b border-ink/10 pb-4">
        <div>
          <h2 className="font-mono text-2xl text-ink">
            Workspace Insights
          </h2>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {canApprove ? (
            <button
              type="button"
              onClick={() => onApprove(discovery.files, discovery.links, discovery.overview)}
              disabled={approving}
              className="small-caps bg-ember px-4 py-2 text-sm text-white hover:bg-ember-hover disabled:opacity-40 transition"
            >
              {approving ? "approving..." : "approve workspace"}
            </button>
          ) : status !== "approved" ? (
            <button
              type="button"
              onClick={onProcess}
              disabled={isProcessingState || files.length === 0}
              className="small-caps border border-neutral-300 px-4 py-2 text-sm hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40 transition"
            >
              {isProcessingState ? "analyzing..." : "analyze files"}
            </button>
          ) : (
            <span className="small-caps border border-emerald-200 bg-emerald-50 px-3 py-2 text-xs text-emerald-700">
              approved
            </span>
          )}
        </div>
      </div>

      {isProcessingState ? (
        <div className="flex flex-col items-center justify-center py-24 text-center">
          <div className="mb-6 h-10 w-10 animate-spin border-2 border-neutral-200 border-t-ember"></div>
          <h3 className="font-mono text-lg text-ink">Analyzing your workspace...</h3>
          {events.length > 0 && (
             <p className="mt-2 text-sm text-neutral-500 animate-pulse">
               {events.at(-1)?.message}
             </p>
          )}
        </div>
      ) : (
        <>


      {files.length === 0 ? (
        <EmptyBlock
          title="Add files first"
          body="Files are the source of the workspace. Upload spreadsheets, then Cerno can generate the first set of insights."
          actionLabel="open files"
          onAction={onOpenFiles}
        />
      ) : null}

      {files.length > 0 && status === "empty" ? (
        <EmptyBlock
          title="Files are uploaded"
          body="Run processing to detect headers, profile columns, generate descriptions, and find relationships."
          actionLabel="process files"
          onAction={onProcess}
        />
      ) : null}

      {overview ? (
        <section className="mt-7 border border-orange-200 bg-orange-50/70 px-5 py-5">
          <div className="small-caps text-sm text-ember">summary</div>
          <div className="mt-3 max-w-4xl text-base leading-7 text-neutral-700">
            <MarkdownText text={overview} />
          </div>
        </section>
      ) : null}

      {insightsReady && (visibleDataDoc || relationshipCards.length > 0) ? (
        <div className="mt-7 grid gap-5 xl:grid-cols-2">
          {visibleDataDoc?.usage_notes && visibleDataDoc.usage_notes.length > 0 && (
             <InsightGroup title="usage notes" items={visibleDataDoc.usage_notes} />
          )}

          <section className="border border-neutral-200 bg-white px-5 py-4">
            <div className="small-caps text-sm text-neutral-500">relationships</div>
            {relationshipCards.length > 0 ? (
              <div className="mt-3 flex flex-col gap-3">
                {relationshipCards.map((rel, idx) => (
                  <div key={idx} className="border border-neutral-100 bg-neutral-50 p-3">
                    <div className="flex items-center gap-2 text-sm">
                      <span className="font-mono text-ink bg-white px-1 border border-neutral-200">{nameForFile(rel.leftFileId, files)}</span>
                      <span className="text-neutral-500">.{rel.leftColumn}</span>
                      <span className="text-ember px-1">→</span>
                      <span className="font-mono text-ink bg-white px-1 border border-neutral-200">{nameForFile(rel.rightFileId, files)}</span>
                      <span className="text-neutral-500">.{rel.rightColumn}</span>
                    </div>
                    {rel.explanation && (
                      <div className="mt-1.5 text-xs text-neutral-600 pl-1 border-l-2 border-orange-200">
                        {rel.explanation}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="mt-3 text-sm text-neutral-500">No relationships generated.</p>
            )}
          </section>

          {visibleDataDoc ? (
            <section className="border border-neutral-200 bg-white px-5 py-4">
              <div className="small-caps text-sm text-neutral-500">glossary</div>
              {visibleDataDoc.glossary.length > 0 ? (
                <div className="mt-3 flex flex-col gap-2">
                  {visibleDataDoc.glossary.map((item, idx) => (
                    <div key={idx} className="flex flex-col gap-0.5 border-b border-neutral-100 pb-2 last:border-0">
                      <div className="font-medium text-sm text-ink">{item.term}</div>
                      <div className="text-xs text-neutral-600">{item.meaning}</div>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="mt-3 text-sm text-neutral-500">No glossary terms generated.</p>
              )}
            </section>
          ) : null}
        </div>
      ) : null}

      {insightsReady && discovery?.files.length ? (
        <section className="mt-7">
          <div className="small-caps text-sm text-neutral-500">file understanding</div>
          <div className="mt-3 grid gap-3">
            {discovery.files.map((file) => (
              <div key={file.file_id} className="border border-neutral-200 bg-white px-4 py-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <h3 className="font-mono text-lg text-ink">{file.friendly_name}</h3>
                    <p className="mt-1 max-w-3xl text-sm leading-6 text-neutral-600">
                      {file.description || "No description generated yet."}
                    </p>
                  </div>
                  <div className="small-caps text-sm text-neutral-400">
                    header row {file.header_row + 1}
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap gap-2">
                  {file.columns.map((column) => (
                    <div
                      key={`${file.file_id}:${column.column_id}`}
                      className="flex items-center gap-1.5 border border-neutral-200 bg-[#fff8f1] px-2 py-1"
                      title={column.description}
                    >
                      <span className="text-sm font-medium text-neutral-700">{column.name}</span>
                      <span className="text-[10px] text-neutral-400 font-mono">{column.dtype}</span>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}

        </>
      )}
    </section>
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
          className="w-full max-w-xl border border-dashed border-neutral-300 bg-white px-8 py-16 text-center transition hover:border-ember disabled:cursor-not-allowed disabled:opacity-50"
        >
          <span className="small-caps text-sm text-ember">new workspace</span>
          <span className="mt-3 block font-mono text-3xl text-ink">
            Upload files to start.
          </span>
          <span className="mt-3 block text-base leading-7 text-neutral-600">
            Add Excel or CSV files. After upload, Cerno will show the process
            action before generating insights.
          </span>
          <span className="small-caps mt-6 inline-block bg-ember px-4 py-3 text-sm text-white">
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
      <div className="flex flex-wrap items-center justify-between gap-5 border-b border-ink/10 pb-4">
        <div>
          <h2 className="font-mono text-2xl text-ink">
            Data Files
          </h2>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={pickFiles}
            disabled={uploading || processing}
            className="small-caps bg-ember px-4 py-3 text-sm text-white hover:bg-ember-hover disabled:opacity-40"
          >
            {uploading ? "uploading..." : "add files"}
          </button>
          <button
            type="button"
            onClick={onProcess}
            disabled={processing || files.length === 0}
            className="small-caps border border-ink px-4 py-3 text-sm hover:bg-neutral-100 disabled:opacity-40"
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
            <article key={file.id} className="border border-neutral-200 bg-white px-5 py-4">
              <div className="flex flex-wrap items-start justify-between gap-4">
                <div className="min-w-0">
                  <h3 className="truncate font-mono text-xl text-ink">
                    {file.friendly_name || file.filename}
                  </h3>
                  <p className="mt-1 max-w-3xl text-sm leading-6 text-neutral-600">
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
                  className="small-caps border border-red-300 px-3 py-2 text-sm text-red-600 hover:bg-red-50 disabled:opacity-40"
                >
                  delete
                </button>
              </div>
              <div className="mt-4 grid gap-3 text-sm text-neutral-600 sm:grid-cols-4">
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
          className="mt-7 block w-full border border-dashed border-neutral-300 bg-white/70 px-5 py-16 text-center text-base text-neutral-500 hover:border-ink"
        >
          Drop files anywhere in the window or click to choose spreadsheets.
        </button>
      )}
    </section>
  );
}

function InsightGroup({ title, items }: { title: string; items: string[] }) {
  return (
    <section className="border border-neutral-200 bg-white px-5 py-4">
      <div className="small-caps text-sm text-neutral-500">{title}</div>
      {items.length > 0 ? (
        <ul className="mt-3 space-y-2 text-sm leading-6 text-neutral-700">
          {items.slice(0, 6).map((item) => (
            <li key={item} className="border-l-2 border-orange-200 pl-3">
              {item}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-sm text-neutral-500">Nothing generated yet.</p>
      )}
    </section>
  );
}

function EmptyBlock({
  title,
  body,
  actionLabel,
  onAction,
}: {
  title: string;
  body: string;
  actionLabel: string;
  onAction: () => void;
}) {
  return (
    <div className="mt-7 border border-dashed border-neutral-300 bg-white/70 px-5 py-8">
      <h3 className="font-mono text-xl text-ink">{title}</h3>
      <p className="mt-2 max-w-2xl text-base leading-7 text-neutral-600">{body}</p>
      <button
        type="button"
        onClick={onAction}
        className="small-caps mt-4 border border-ink px-3 py-2 text-sm hover:border-ember hover:text-ember"
      >
        {actionLabel}
      </button>
    </div>
  );
}

function FileStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-neutral-200 bg-white px-4 py-3">
      <div className="small-caps text-sm text-neutral-500">{label}</div>
      <div className="mt-2 font-mono text-2xl text-ink">{value}</div>
    </div>
  );
}

function FileFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="small-caps text-sm text-neutral-400">{label}</div>
      <div className="mt-1 text-base text-ink">{value}</div>
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

function nameForFile(fileId: string, files: FileRecord[]) {
  const file = files.find((item) => item.id === fileId);
  return file?.friendly_name || file?.filename || "file";
}

function defaultTurnTitle(turn: ChatTurn): string {
  const trimmed = (turn.title || turn.user_message).trim();
  return trimmed.length > 52 ? `${trimmed.slice(0, 49)}...` : trimmed || "Untitled chat";
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
