import { useMemo, useRef } from "react";
import type {
  ChatTurn,
  DashboardCell,
  DashboardPage,
  DataDoc,
  DiscoveryResponse,
  DiscoveryStatus,
  FileRecord,
  Link,
  ProcessingEvent,
  Session,
  Widget,
} from "../lib/types";
import { widgetFromCell } from "../lib/widgets";
import { MarkdownText } from "./ChatSidebar";
import { WidgetRenderer } from "./WidgetRenderer";

type WorkspaceSidebarProps = {
  session: Session;
  files: FileRecord[];
  turns: ChatTurn[];
  onSelectTab: (tab: "ask" | "insights" | "files") => void;
};

export function WorkspaceSidebar({
  session,
  files,
  turns,
  onSelectTab,
}: WorkspaceSidebarProps) {
  return (
    <aside className="flex h-full flex-col">
      <div className="hairline border-b px-5 py-4">
        <div className="min-w-0">
          <div className="small-caps text-sm text-neutral-400">workspace</div>
          <div className="mt-1 truncate font-mono text-lg text-ink">{session.name}</div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {files.length > 0 ? (
          <>
            <div className="small-caps text-sm text-neutral-500">files</div>
            <div className="mt-3 grid gap-2">
              {files.slice(0, 8).map((file) => (
              <button
                key={file.id}
                type="button"
                onClick={() => onSelectTab("files")}
                className="min-w-0 border border-neutral-200 bg-white px-3 py-2 text-left transition hover:border-ember"
              >
                <div className="truncate text-sm text-ink">
                  {file.friendly_name || file.filename}
                </div>
                <div className="mt-1 text-xs text-neutral-500">
                  {formatCompactNumber(file.row_count)} rows
                </div>
              </button>
              ))}
            </div>
          </>
        ) : null}

        {turns.length > 0 ? (
          <>
            <div
              className={
                files.length > 0
                  ? "mt-6 small-caps text-sm text-neutral-500"
                  : "small-caps text-sm text-neutral-500"
              }
            >
              chats
            </div>
            <div className="mt-3 grid gap-2">
              {turns.slice(0, 10).map((turn) => (
              <button
                key={turn.id}
                type="button"
                onClick={() => onSelectTab("ask")}
                className="min-w-0 border border-neutral-200 bg-white px-3 py-2 text-left transition hover:border-ember"
              >
                <div className="line-clamp-2 text-sm leading-5 text-ink">
                  {turn.user_message}
                </div>
                <div className="mt-1 small-caps text-xs text-neutral-400">
                  {turn.state}
                </div>
              </button>
              ))}
            </div>
          </>
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
  building: boolean;
  onProcess: () => void;
  onApprove: (
    files: DiscoveryResponse["files"],
    links: DiscoveryResponse["links"],
    overview: string,
  ) => void;
  onBuildDashboard: () => void;
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
  building,
  onProcess,
  onApprove,
  onBuildDashboard,
  onOpenFiles,
}: InsightsPanelProps) {
  const status = discovery?.status ?? "empty";
  const canApprove = discovery?.status === "pending_review";
  const overview = dataDoc?.overview || discovery?.overview;

  return (
    <section className="px-8 py-7">
      <div className="flex flex-wrap items-start justify-between gap-5 border-b border-ink/10 pb-5">
        <div>
          <div className="small-caps text-sm text-ember">insights</div>
          <h2 className="mt-2 font-mono text-3xl text-ink">
            What Cerno found while processing.
          </h2>
          <p className="mt-2 max-w-3xl text-base leading-7 text-neutral-600">
            These are generated from the file understanding step. Approve them,
            ask follow-ups, or generate visuals from the right panel.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusPill label={statusLabel(status, processing)} />
          <button
            type="button"
            onClick={onOpenFiles}
            className="small-caps border border-neutral-300 px-3 py-2 text-sm hover:border-ink"
          >
            files
          </button>
          <button
            type="button"
            onClick={onProcess}
            disabled={processing || files.length === 0}
            className="small-caps border border-ink px-3 py-2 text-sm hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {processing ? "processing..." : "refresh insights"}
          </button>
          {canApprove ? (
            <button
              type="button"
              onClick={() => onApprove(discovery.files, discovery.links, discovery.overview)}
              disabled={approving}
              className="small-caps bg-ember px-3 py-2 text-sm text-white hover:bg-ember-hover disabled:opacity-40"
            >
              {approving ? "approving..." : "approve insights"}
            </button>
          ) : null}
          {status === "approved" ? (
            <button
              type="button"
              onClick={onBuildDashboard}
              disabled={building}
              className="small-caps border border-ink bg-white px-3 py-2 text-sm hover:border-ember hover:text-ember disabled:opacity-40"
            >
              {building ? "building..." : "generate visuals"}
            </button>
          ) : null}
        </div>
      </div>

      {files.length === 0 ? (
        <EmptyBlock
          title="Add files first"
          body="Files are the source of the workspace. Upload spreadsheets, then Cerno can generate the first set of insights."
          actionLabel="open files"
          onAction={onOpenFiles}
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

      {dataDoc ? (
        <div className="mt-7 grid gap-5 xl:grid-cols-2">
          <InsightGroup title="usage notes" items={dataDoc.usage_notes} />
          <InsightGroup title="starter questions" items={dataDoc.starter_questions} />
          <InsightGroup
            title="relationships"
            items={dataDoc.relationships.map(
              (rel) =>
                `${nameForFile(rel.left_file_id, files)}.${rel.left_column} links to ${nameForFile(
                  rel.right_file_id,
                  files,
                )}.${rel.right_column}: ${rel.explanation}`,
            )}
          />
          <InsightGroup
            title="glossary"
            items={dataDoc.glossary.map((item) => `${item.term}: ${item.meaning}`)}
          />
        </div>
      ) : null}

      {discovery?.files.length ? (
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
                  {file.columns.slice(0, 10).map((column) => (
                    <span
                      key={`${file.file_id}:${column.column_id}`}
                      className="border border-neutral-200 bg-[#fff8f1] px-2 py-1 text-sm text-neutral-700"
                    >
                      {column.name}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className="mt-7 border-t border-ink/10 pt-5">
        <div className="small-caps text-sm text-neutral-500">processing log</div>
        <div className="mt-3 grid gap-2">
          {events.length > 0 ? (
            events.slice(-8).reverse().map((event) => (
              <div key={event.id} className="flex gap-3 border border-neutral-200 bg-white px-3 py-2 text-sm">
                <span className="small-caps shrink-0 text-neutral-400">{event.kind}</span>
                <span className="text-neutral-700">{event.message}</span>
              </div>
            ))
          ) : (
            <div className="text-sm text-neutral-500">
              No processing events yet.
            </div>
          )}
        </div>
      </section>

      {links.length > 0 ? (
        <div className="mt-5 text-sm text-neutral-500">
          {links.length} relationship{links.length === 1 ? "" : "s"} saved in this workspace.
        </div>
      ) : null}
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
      <div className="flex flex-wrap items-start justify-between gap-5 border-b border-ink/10 pb-5">
        <div>
          <div className="small-caps text-sm text-ember">files</div>
          <h2 className="mt-2 font-mono text-3xl text-ink">
            Manage the data in this workspace.
          </h2>
          <p className="mt-2 max-w-3xl text-base leading-7 text-neutral-600">
            Add or remove files here. When the file list changes, run processing
            again so Cerno refreshes the workspace understanding.
          </p>
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

type VisualPanelProps = {
  pages: DashboardPage[];
  cellsByPage: Record<string, DashboardCell[]>;
  selectedPageId: string | null;
  canBuild: boolean;
  building: boolean;
  onSelectPage: (pageId: string) => void;
  onBuildDashboard: () => void;
};

export function VisualPanel({
  pages,
  cellsByPage,
  selectedPageId,
  canBuild,
  building,
  onSelectPage,
  onBuildDashboard,
}: VisualPanelProps) {
  const pagesWithWidgets = useMemo(
    () =>
      pages
        .map((page) => ({
          page,
          widgets: (cellsByPage[page.id] ?? [])
            .map(widgetFromCell)
            .filter((widget): widget is Widget => Boolean(widget)),
        }))
        .filter((item) => item.widgets.length > 0),
    [cellsByPage, pages],
  );
  const selected =
    pagesWithWidgets.find((item) => item.page.id === selectedPageId) ??
    pagesWithWidgets[0] ??
    null;

  return (
    <div className="flex min-h-full flex-col">
      <div className="hairline border-b px-5 py-4">
        <div className="small-caps text-sm text-ember">selected visuals</div>
        <p className="mt-2 text-sm leading-6 text-neutral-600">
          Charts generated from Ask or Insights appear here for inspection.
        </p>
      </div>

      {pagesWithWidgets.length > 0 ? (
        <>
          <div className="hairline border-b px-5 py-4">
            <div className="grid gap-2">
              {pagesWithWidgets.map(({ page, widgets }) => (
                <button
                  key={page.id}
                  type="button"
                  onClick={() => onSelectPage(page.id)}
                  className={`border px-3 py-2 text-left transition ${
                    selected?.page.id === page.id
                      ? "border-ember bg-orange-50"
                      : "border-neutral-200 hover:border-neutral-300"
                  }`}
                >
                  <div className="truncate font-mono text-sm text-ink">{page.title}</div>
                  <div className="mt-1 small-caps text-xs text-neutral-400">
                    {widgets.length} visual{widgets.length === 1 ? "" : "s"}
                  </div>
                </button>
              ))}
            </div>
          </div>
          <div className="flex-1 px-5 py-4">
            {selected?.widgets.map((widget, index) => (
              <div key={`${selected.page.id}:${index}`} className="mb-5 border border-neutral-200 bg-white px-4 py-3">
                <WidgetRenderer widget={widget} height={280} />
              </div>
            ))}
          </div>
        </>
      ) : (
        <div className="px-5 py-6">
          <div className="border border-dashed border-neutral-300 bg-[#fff8f1] px-4 py-8 text-sm leading-6 text-neutral-600">
            No saved visuals yet. Ask Cerno for a chart, or generate visuals
            after approving the workspace insights.
          </div>
          <button
            type="button"
            onClick={onBuildDashboard}
            disabled={building || !canBuild}
            className="small-caps mt-4 w-full border border-ink px-3 py-2 text-sm hover:bg-neutral-100 disabled:opacity-40"
          >
            {building
              ? "building..."
              : canBuild
                ? "generate visuals"
                : "approve insights first"}
          </button>
        </div>
      )}
    </div>
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

function StatusPill({ label }: { label: string }) {
  return (
    <span className="small-caps border border-orange-200 bg-orange-50 px-3 py-2 text-sm text-ember">
      {label}
    </span>
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
