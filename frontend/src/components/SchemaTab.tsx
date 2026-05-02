import { useEffect, useMemo, useState } from "react";
import { getFilePreview } from "../lib/api";
import type {
  DiscoveredColumn,
  DiscoveredFile,
  DiscoveredLink,
  DiscoveryResponse,
  FilePreviewResponse,
  FileRecord,
  Link,
  LinkDirection,
  ProcessingEvent,
  ProcessingEventKind,
  SimpleDtype,
} from "../lib/types";

type Props = {
  files: FileRecord[];
  links: Link[];
  discovery: DiscoveryResponse | null;
  events: ProcessingEvent[];
  processing: boolean;
  approving: boolean;
  canProcess: boolean;
  onProcess: () => void;
  onApprove: (
    files: DiscoveredFile[],
    links: DiscoveredLink[],
    overview: string,
  ) => void;
};

const DTYPES: SimpleDtype[] = [
  "string",
  "int",
  "float",
  "date",
  "datetime",
  "bool",
  "category",
];

const DIRECTIONS: LinkDirection[] = [
  "many_to_one",
  "one_to_one",
  "many_to_many",
];

const PHASES: { label: string; kinds: ProcessingEventKind[]; note: string }[] = [
  {
    label: "Reading files",
    kinds: ["started", "reading_files"],
    note: "Detecting sheets and sample rows.",
  },
  {
    label: "Understanding the model",
    kinds: ["calling_llm", "parsing_response"],
    note: "Identifying headers, meanings, and likely relationships.",
  },
  {
    label: "Preparing review",
    kinds: ["saving_schema", "done"],
    note: "Saving the draft so you can inspect it.",
  },
];

export function SchemaTab({
  files,
  links: storedLinks,
  discovery,
  events,
  processing,
  approving,
  canProcess,
  onProcess,
  onApprove,
}: Props) {
  const [editing, setEditing] = useState(false);
  const [draftFiles, setDraftFiles] = useState<DiscoveredFile[]>([]);
  const [draftLinks, setDraftLinks] = useState<DiscoveredLink[]>([]);
  const [draftOverview, setDraftOverview] = useState("");
  const [activeFileId, setActiveFileId] = useState<string | null>(null);
  const [preview, setPreview] = useState<FilePreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  useEffect(() => {
    if (discovery && !editing) {
      setDraftFiles(discovery.files);
      setDraftLinks(discovery.links);
      setDraftOverview(discovery.overview);
      setActiveFileId((current) => current ?? discovery.files[0]?.file_id ?? null);
    }
  }, [discovery, editing]);

  useEffect(() => {
    if (!activeFileId) return;
    setPreview(null);
    setPreviewError(null);
    setPreviewLoading(true);
    getFilePreview(activeFileId, 50)
      .then((p) => setPreview(p))
      .catch((err: Error) => setPreviewError(err.message))
      .finally(() => setPreviewLoading(false));
  }, [activeFileId]);

  const fileNameMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const f of files) m.set(f.id, f.friendly_name || f.filename);
    for (const df of draftFiles) {
      const friendly = df.friendly_name?.trim();
      if (friendly) m.set(df.file_id, friendly);
    }
    return m;
  }, [files, draftFiles]);

  const status = discovery?.status ?? "empty";
  const activeFile =
    draftFiles.find((file) => file.file_id === activeFileId) ??
    draftFiles[0] ??
    null;
  const activeFileIdx = activeFile
    ? draftFiles.findIndex((file) => file.file_id === activeFile.file_id)
    : -1;

  const columnCount = draftFiles.reduce((sum, file) => sum + file.columns.length, 0);
  const linkStats = useMemo(() => {
    const stats = new Map<string, LinkStat>();
    for (const link of storedLinks) {
      stats.set(linkLookupKey(link.file_a, link.col_a, link.file_b, link.col_b), {
        score: link.score,
        overlap: link.overlap,
        source: link.source,
      });
    }
    return stats;
  }, [storedLinks]);

  const handleCancelEdit = () => {
    if (discovery) {
      setDraftFiles(discovery.files);
      setDraftLinks(discovery.links);
      setDraftOverview(discovery.overview);
    }
    setEditing(false);
  };

  const handleApprove = () => {
    onApprove(draftFiles, draftLinks, draftOverview);
    setEditing(false);
  };

  const updateFile = (idx: number, patch: Partial<DiscoveredFile>) => {
    setDraftFiles((prev) =>
      prev.map((file, i) => (i === idx ? { ...file, ...patch } : file)),
    );
  };

  const updateColumn = (
    fileIdx: number,
    colIdx: number,
    patch: Partial<DiscoveredColumn>,
  ) => {
    setDraftFiles((prev) =>
      prev.map((file, i) =>
        i === fileIdx
          ? {
              ...file,
              columns: file.columns.map((col, j) =>
                j === colIdx ? { ...col, ...patch } : col,
              ),
            }
          : file,
      ),
    );
  };

  const addColumn = (fileIdx: number) => {
    setDraftFiles((prev) =>
      prev.map((file, i) =>
        i === fileIdx
          ? {
              ...file,
              columns: [
                ...file.columns,
                {
                  column_id: `col_${file.columns.length + 1}`,
                  name: "new_column",
                  description: "",
                  dtype: "string",
                },
              ],
            }
          : file,
      ),
    );
  };

  const removeColumn = (fileIdx: number, colIdx: number) => {
    setDraftFiles((prev) =>
      prev.map((file, i) =>
        i === fileIdx
          ? { ...file, columns: file.columns.filter((_, j) => j !== colIdx) }
          : file,
      ),
    );
  };

  const updateLink = (idx: number, patch: Partial<DiscoveredLink>) => {
    setDraftLinks((prev) =>
      prev.map((link, i) => (i === idx ? { ...link, ...patch } : link)),
    );
  };

  const addLink = () => {
    const a = draftFiles[0];
    const b = draftFiles[1];
    if (!a || !b) return;
    setDraftLinks((prev) => [
      ...prev,
      {
        file_a_id: a.file_id,
        col_a: a.columns[0]?.name ?? "",
        file_b_id: b.file_id,
        col_b: b.columns[0]?.name ?? "",
        direction: "many_to_one",
        summary: "",
      },
    ]);
  };

  const removeLink = (idx: number) => {
    setDraftLinks((prev) => prev.filter((_, i) => i !== idx));
  };

  return (
    <div className="px-8 py-6">
      <header className="hairline mb-5 flex flex-wrap items-center justify-between gap-3 border-b pb-4">
        <div>
          <div className="small-caps text-xs text-neutral-500">schema</div>
          <h2 className="font-mono text-2xl text-ink">{schemaTitle(status, processing)}</h2>
        </div>
        <div className="flex items-center gap-2">
          {status === "empty" || status === "failed" ? (
            <button
              type="button"
              onClick={onProcess}
              disabled={!canProcess || processing}
              className="small-caps border border-ink bg-ember px-3 py-1.5 text-xs text-white hover:bg-ember-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              {processing ? "processing" : "process files"}
            </button>
          ) : null}
          {status === "pending_review" || status === "approved" ? (
            editing ? (
              <>
                <button
                  type="button"
                  onClick={handleCancelEdit}
                  disabled={approving}
                  className="small-caps border border-ink px-3 py-1.5 text-xs hover:bg-neutral-100 disabled:opacity-40"
                >
                  cancel
                </button>
                <button
                  type="button"
                  onClick={handleApprove}
                  disabled={approving}
                  className="small-caps border border-ink bg-ember px-3 py-1.5 text-xs text-white hover:bg-ember-hover disabled:opacity-40"
                >
                  {approving ? "applying" : "save and apply"}
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className="small-caps border border-ink px-3 py-1.5 text-xs hover:bg-neutral-100"
                >
                  edit
                </button>
                {status === "pending_review" ? (
                  <button
                    type="button"
                    onClick={handleApprove}
                    disabled={approving}
                    className="small-caps border border-ink bg-ember px-3 py-1.5 text-xs text-white hover:bg-ember-hover disabled:opacity-40"
                  >
                    {approving ? "approving" : "approve"}
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={onProcess}
                    disabled={processing}
                    className="small-caps border border-ink px-3 py-1.5 text-xs hover:bg-neutral-100 disabled:opacity-40"
                  >
                    re-process
                  </button>
                )}
              </>
            )
          ) : null}
        </div>
      </header>

      {processing || events.length > 0 ? (
        <ProcessingCheckpoints events={events} processing={processing} />
      ) : null}

      {status === "empty" && !processing ? (
        <EmptyState canProcess={canProcess} />
      ) : null}

      {discovery && (status === "pending_review" || status === "approved") ? (
        <div className="space-y-6">
          <section className="hairline border bg-white p-4">
            <div className="small-caps text-xs text-neutral-500">generated overview</div>
            {editing ? (
              <textarea
                value={draftOverview}
                onChange={(e) => setDraftOverview(e.target.value)}
                rows={4}
                className="mt-3 w-full border border-neutral-300 bg-white p-3 text-sm focus:outline-none focus:ring-1 focus:ring-ink"
              />
            ) : (
              <p className="mt-2 max-w-5xl text-sm leading-6 text-neutral-700">
                {draftOverview || "No overview was generated."}
              </p>
            )}
          </section>

          <section className="grid gap-3 md:grid-cols-4">
            <Metric label="files" value={draftFiles.length} />
            <Metric label="columns" value={columnCount} />
            <Metric label="relationships" value={draftLinks.length} />
            <Metric
              label="state"
              value={status === "approved" ? "approved" : "draft"}
              text
            />
          </section>

          <section className="hairline border bg-white">
            <div className="hairline flex items-center justify-between border-b px-4 py-3">
              <div>
                <h3 className="font-mono text-base text-ink">Relationships</h3>
                <p className="mt-0.5 text-xs text-neutral-500">
                  A readable map of which columns Cerno thinks connect your files.
                </p>
              </div>
              {editing ? (
                <button
                  type="button"
                  onClick={addLink}
                  disabled={draftFiles.length < 2}
                  className="small-caps border border-ink px-2 py-1 text-[11px] hover:bg-neutral-100 disabled:opacity-40"
                >
                  add link
                </button>
              ) : null}
            </div>
            <RelationshipMap
              files={draftFiles}
              links={draftLinks}
              linkStats={linkStats}
              fileNameMap={fileNameMap}
              onSelectFile={setActiveFileId}
            />
          </section>

          <section className="grid gap-6 xl:grid-cols-[22rem_minmax(0,1fr)]">
            <FileRail
              files={draftFiles}
              activeFileId={activeFile?.file_id ?? null}
              fileNameMap={fileNameMap}
              onSelectFile={setActiveFileId}
            />
            {activeFile && activeFileIdx >= 0 ? (
              <FileDetailPanel
                file={activeFile}
                fileIdx={activeFileIdx}
                editing={editing}
                preview={preview}
                previewLoading={previewLoading}
                previewError={previewError}
                links={draftLinks}
                draftFiles={draftFiles}
                fileNameMap={fileNameMap}
                onUpdateFile={updateFile}
                onUpdateColumn={updateColumn}
                onAddColumn={addColumn}
                onRemoveColumn={removeColumn}
                onUpdateLink={updateLink}
                onRemoveLink={removeLink}
              />
            ) : null}
          </section>

        </div>
      ) : null}
    </div>
  );
}

function schemaTitle(status: string, processing: boolean): string {
  if (processing || status === "discovering") return "Building data model";
  if (status === "pending_review") return "Review data model";
  if (status === "approved") return "Approved data model";
  if (status === "failed") return "Processing needs attention";
  return "No data model yet";
}

function EmptyState({ canProcess }: { canProcess: boolean }) {
  return (
    <div className="mt-10 max-w-xl border border-neutral-200 bg-white p-5">
      <h3 className="font-mono text-base text-ink">Start with your uploaded files</h3>
      <p className="mt-2 text-sm leading-6 text-neutral-600">
        {canProcess
          ? "Cerno will identify headers, column meanings, data types, and likely relationships between your files."
          : "Upload one or more files first, then Cerno can build the data model."}
      </p>
    </div>
  );
}

function ProcessingCheckpoints({
  events,
  processing,
}: {
  events: ProcessingEvent[];
  processing: boolean;
}) {
  const latestError =
    [...events].reverse().find((event: ProcessingEvent) => event.kind === "error") ??
    null;
  const completedKinds = new Set(events.map((event) => event.kind));
  const currentPhase = completedKinds.has("done")
    ? 2
    : completedKinds.has("calling_llm") || completedKinds.has("parsing_response")
      ? 1
      : 0;
  const progress = completedKinds.has("done") ? 100 : currentPhase === 1 ? 58 : 24;
  const current = PHASES[currentPhase] ?? PHASES[0]!;

  return (
    <section className="mb-6 border border-neutral-200 bg-white p-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h3 className="font-mono text-sm text-ink">
            {latestError ? "Processing stopped" : current.label}
          </h3>
          <p className="mt-1 text-xs text-neutral-500">
            {latestError
              ? "Review the message below and try again."
              : current.note}
          </p>
        </div>
        <div className="small-caps text-xs text-neutral-500">
          {completedKinds.has("done") ? "complete" : "in progress"}
        </div>
      </div>
      <div className="relative mt-4 h-2 overflow-hidden bg-neutral-100">
        <div
          className="h-full bg-ember transition-all duration-700"
          style={{ width: `${progress}%` }}
        />
        {processing && !completedKinds.has("done") && !latestError ? (
          <div className="absolute inset-y-0 left-0 w-1/3 animate-pulse bg-ember/40" />
        ) : null}
      </div>
      <ol className="mt-4 grid gap-2 md:grid-cols-3">
        {PHASES.map((phase, idx) => {
          const complete = phase.kinds.some((kind) => completedKinds.has(kind));
          const active = idx === currentPhase && processing && !latestError;
          return (
            <li
              key={phase.label}
              className={`border px-3 py-2 ${
                complete && idx < currentPhase
                  ? "border-ember/40 bg-ember/5"
                  : active
                    ? "border-ink bg-neutral-50"
                    : "border-neutral-200 bg-white"
              }`}
            >
              <div className="flex items-center gap-2">
                <span
                  className={`h-2 w-2 rounded-full ${
                    complete && idx < currentPhase
                      ? "bg-ember"
                      : active
                        ? "bg-ink"
                        : "bg-neutral-300"
                  }`}
                />
                <span className="small-caps text-[11px] text-neutral-500">
                  {String(idx + 1).padStart(2, "0")}
                </span>
              </div>
              <div className="mt-2 text-xs text-ink">{phase.label}</div>
              <div className="mt-1 text-[11px] text-neutral-500">{phase.note}</div>
            </li>
          );
        })}
      </ol>
      {latestError ? (
        <div className="mt-4 border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {friendlyEventMessage(latestError)}
        </div>
      ) : null}
    </section>
  );
}

type LinkStat = {
  score?: number;
  overlap?: number;
  source?: string;
};

type RelationshipGroup = {
  key: string;
  leftId: string;
  rightId: string;
  links: DiscoveredLink[];
};

function RelationshipMap({
  files,
  links,
  linkStats,
  fileNameMap,
  onSelectFile,
}: {
  files: DiscoveredFile[];
  links: DiscoveredLink[];
  linkStats: Map<string, LinkStat>;
  fileNameMap: Map<string, string>;
  onSelectFile: (fileId: string) => void;
}) {
  const groups = useMemo(() => groupRelationships(files, links), [files, links]);

  return (
    <div className="p-4">
      {groups.length === 0 ? (
        <div className="border border-neutral-200 bg-neutral-50 p-4 text-sm text-neutral-600">
          Cerno did not find strong cross-file relationships yet. You can still edit
          the model and add one manually.
        </div>
      ) : (
        <div className="space-y-4">
          {groups.map((group) => (
            <div key={group.key} className="border border-neutral-200">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-neutral-200 bg-neutral-50 px-3 py-2">
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => onSelectFile(group.leftId)}
                    className="truncate font-mono text-sm text-ink hover:text-ember"
                  >
                    {fileNameMap.get(group.leftId) ?? group.leftId}
                  </button>
                  <span className="text-neutral-400">{"->"}</span>
                  <button
                    type="button"
                    onClick={() => onSelectFile(group.rightId)}
                    className="truncate font-mono text-sm text-ink hover:text-ember"
                  >
                    {fileNameMap.get(group.rightId) ?? group.rightId}
                  </button>
                </div>
                <div className="small-caps text-[11px] text-neutral-500">
                  {group.links.length} link{group.links.length === 1 ? "" : "s"}
                </div>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full min-w-[44rem] border-collapse text-sm">
                  <thead>
                    <tr className="border-b border-neutral-100">
                      <Th>source</Th>
                      <Th>target</Th>
                      <Th>type</Th>
                      <Th>evidence</Th>
                      <Th>why it matters</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {group.links.map((link, idx) => {
                      const stat = linkStats.get(
                        linkLookupKey(link.file_a_id, link.col_a, link.file_b_id, link.col_b),
                      );
                      return (
                        <tr
                          key={`${link.file_a_id}-${link.col_a}-${idx}`}
                          className="border-b border-neutral-100 last:border-b-0"
                        >
                          <Td mono>
                            <button
                              type="button"
                              onClick={() => onSelectFile(link.file_a_id)}
                              className="hover:text-ember"
                            >
                              {fileNameMap.get(link.file_a_id) ?? link.file_a_id}.
                              {link.col_a}
                            </button>
                          </Td>
                          <Td mono>
                            <button
                              type="button"
                              onClick={() => onSelectFile(link.file_b_id)}
                              className="hover:text-ember"
                            >
                              {fileNameMap.get(link.file_b_id) ?? link.file_b_id}.
                              {link.col_b}
                            </button>
                          </Td>
                          <Td>
                            <span className="small-caps text-xs text-neutral-500">
                              {link.direction.replace(/_/g, " ")}
                            </span>
                          </Td>
                          <Td>
                            <EvidenceBar stat={stat} />
                          </Td>
                          <Td>
                            <span className="text-xs leading-5 text-neutral-600">
                              {link.summary || "Potential matching identifier."}
                            </span>
                          </Td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function EvidenceBar({ stat }: { stat?: LinkStat }) {
  const score = stat?.score ?? stat?.overlap;
  if (score === undefined) {
    return <span className="small-caps text-[11px] text-neutral-400">semantic</span>;
  }
  const pct = Math.max(0, Math.min(100, Math.round(score * 100)));
  const source = stat?.source === "user_added" ? "manual" : "value scan";
  return (
    <div className="min-w-[8rem]">
      <div className="h-1.5 bg-neutral-100">
        <div className="h-full bg-ember" style={{ width: `${pct}%` }} />
      </div>
      <div className="small-caps mt-1 text-[10px] text-neutral-500">
        {pct}% {source}
      </div>
    </div>
  );
}

function FileRail({
  files,
  activeFileId,
  fileNameMap,
  onSelectFile,
}: {
  files: DiscoveredFile[];
  activeFileId: string | null;
  fileNameMap: Map<string, string>;
  onSelectFile: (fileId: string) => void;
}) {
  return (
    <aside className="hairline border bg-white">
      <div className="hairline border-b px-4 py-3">
        <div className="small-caps text-xs text-neutral-500">files</div>
      </div>
      <div className="divide-y divide-neutral-100">
        {files.map((file) => (
          <button
            key={file.file_id}
            type="button"
            onClick={() => onSelectFile(file.file_id)}
            className={`block w-full px-4 py-3 text-left ${
              activeFileId === file.file_id ? "bg-neutral-50" : "hover:bg-neutral-50"
            }`}
          >
            <div className="truncate font-mono text-sm text-ink">
              {fileNameMap.get(file.file_id) ?? file.friendly_name}
            </div>
            <div className="mt-1 text-xs text-neutral-500">
              {file.columns.length} columns, header row {file.header_row}
            </div>
          </button>
        ))}
      </div>
    </aside>
  );
}

function FileDetailPanel({
  file,
  fileIdx,
  editing,
  preview,
  previewLoading,
  previewError,
  links,
  draftFiles,
  fileNameMap,
  onUpdateFile,
  onUpdateColumn,
  onAddColumn,
  onRemoveColumn,
  onUpdateLink,
  onRemoveLink,
}: {
  file: DiscoveredFile;
  fileIdx: number;
  editing: boolean;
  preview: FilePreviewResponse | null;
  previewLoading: boolean;
  previewError: string | null;
  links: DiscoveredLink[];
  draftFiles: DiscoveredFile[];
  fileNameMap: Map<string, string>;
  onUpdateFile: (idx: number, patch: Partial<DiscoveredFile>) => void;
  onUpdateColumn: (
    fileIdx: number,
    colIdx: number,
    patch: Partial<DiscoveredColumn>,
  ) => void;
  onAddColumn: (fileIdx: number) => void;
  onRemoveColumn: (fileIdx: number, colIdx: number) => void;
  onUpdateLink: (idx: number, patch: Partial<DiscoveredLink>) => void;
  onRemoveLink: (idx: number) => void;
}) {
  const fileLinks = links
    .map((link, idx) => ({ link, idx }))
    .filter(({ link }) => link.file_a_id === file.file_id || link.file_b_id === file.file_id);

  return (
    <section className="min-w-0 space-y-6">
      <div className="hairline border bg-white">
        <div className="hairline border-b px-4 py-3">
          <div className="small-caps text-xs text-neutral-500">file detail</div>
          {editing ? (
            <input
              type="text"
              value={file.friendly_name}
              onChange={(e) => onUpdateFile(fileIdx, { friendly_name: e.target.value })}
              className="mt-1 w-full border border-neutral-300 px-2 py-1 font-mono text-base focus:outline-none focus:ring-1 focus:ring-ink"
            />
          ) : (
            <h3 className="mt-1 font-mono text-lg text-ink">{file.friendly_name}</h3>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-neutral-500">
            <span>
              Header row{" "}
              {editing ? (
                <input
                  type="number"
                  min={0}
                  value={file.header_row}
                  onChange={(e) =>
                    onUpdateFile(fileIdx, {
                      header_row: Math.max(0, Number(e.target.value) || 0),
                    })
                  }
                  className="ml-1 w-16 border border-neutral-300 px-1 py-0.5"
                />
              ) : (
                file.header_row
              )}
            </span>
            <span>{file.columns.length} columns</span>
          </div>
          {editing ? (
            <textarea
              value={file.description}
              onChange={(e) => onUpdateFile(fileIdx, { description: e.target.value })}
              rows={2}
              className="mt-3 w-full border border-neutral-300 p-2 text-sm focus:outline-none focus:ring-1 focus:ring-ink"
            />
          ) : file.description ? (
            <p className="mt-3 max-w-3xl text-sm leading-6 text-neutral-600">
              {file.description}
            </p>
          ) : null}
        </div>

        <div className="overflow-x-auto">
          <table className="w-full min-w-[46rem] border-collapse text-sm">
            <thead className="bg-neutral-50">
              <tr>
                <Th>column</Th>
                <Th>meaning</Th>
                <Th>type</Th>
                {editing ? <Th>{""}</Th> : null}
              </tr>
            </thead>
            <tbody>
              {file.columns.map((col, colIdx) => (
                <tr key={`${col.column_id}-${colIdx}`} className="hairline border-b">
                  <Td mono>
                    {editing ? (
                      <input
                        type="text"
                        value={col.name}
                        onChange={(e) =>
                          onUpdateColumn(fileIdx, colIdx, { name: e.target.value })
                        }
                        className="w-full border border-neutral-300 px-2 py-1 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-ink"
                      />
                    ) : (
                      col.name
                    )}
                  </Td>
                  <Td>
                    {editing ? (
                      <input
                        type="text"
                        value={col.description}
                        onChange={(e) =>
                          onUpdateColumn(fileIdx, colIdx, {
                            description: e.target.value,
                          })
                        }
                        className="w-full border border-neutral-300 px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-ink"
                      />
                    ) : (
                      col.description || <span className="text-neutral-400">Not described</span>
                    )}
                  </Td>
                  <Td>
                    {editing ? (
                      <select
                        value={col.dtype}
                        onChange={(e) =>
                          onUpdateColumn(fileIdx, colIdx, {
                            dtype: e.target.value as SimpleDtype,
                          })
                        }
                        className="border border-neutral-300 bg-white px-2 py-1 text-xs"
                      >
                        {DTYPES.map((dtype) => (
                          <option key={dtype} value={dtype}>
                            {dtype}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className="small-caps text-xs text-neutral-500">
                        {col.dtype}
                      </span>
                    )}
                  </Td>
                  {editing ? (
                    <Td>
                      <button
                        type="button"
                        onClick={() => onRemoveColumn(fileIdx, colIdx)}
                        className="small-caps text-xs text-red-600 hover:underline"
                      >
                        remove
                      </button>
                    </Td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {editing ? (
          <div className="px-4 py-3">
            <button
              type="button"
              onClick={() => onAddColumn(fileIdx)}
              className="small-caps border border-ink px-2 py-1 text-xs hover:bg-neutral-100"
            >
              add column
            </button>
          </div>
        ) : null}
      </div>

      <div className="hairline border bg-white">
        <div className="hairline flex items-center justify-between border-b px-4 py-3">
          <div>
            <div className="small-caps text-xs text-neutral-500">preview</div>
            <h3 className="mt-1 font-mono text-base text-ink">Rows after detection</h3>
          </div>
          {preview ? (
            <div className="small-caps text-xs text-neutral-500">
              {preview.total_rows.toLocaleString()} rows
            </div>
          ) : null}
        </div>
        {previewLoading ? (
          <div className="p-4 text-sm text-neutral-500">Loading preview...</div>
        ) : previewError ? (
          <div className="p-4 text-sm text-red-600">{previewError}</div>
        ) : preview ? (
          <PreviewTable preview={preview} />
        ) : null}
      </div>

      <RelationshipList
        links={fileLinks}
        draftFiles={draftFiles}
        fileNameMap={fileNameMap}
        editing={editing}
        onUpdateLink={onUpdateLink}
        onRemoveLink={onRemoveLink}
      />
    </section>
  );
}

function RelationshipList({
  links,
  draftFiles,
  fileNameMap,
  editing,
  onUpdateLink,
  onRemoveLink,
}: {
  links: { link: DiscoveredLink; idx: number }[];
  draftFiles: DiscoveredFile[];
  fileNameMap: Map<string, string>;
  editing: boolean;
  onUpdateLink: (idx: number, patch: Partial<DiscoveredLink>) => void;
  onRemoveLink: (idx: number) => void;
}) {
  return (
    <section className="hairline border bg-white">
      <div className="hairline border-b px-4 py-3">
        <div className="small-caps text-xs text-neutral-500">relationships for file</div>
      </div>
      {links.length === 0 ? (
        <div className="p-4 text-sm text-neutral-500">No relationships for this file.</div>
      ) : (
        <div className="divide-y divide-neutral-100">
          {links.map(({ link, idx }) => (
            <div key={`${idx}-${link.col_a}-${link.col_b}`} className="p-4">
              {editing ? (
                <div className="grid gap-3 md:grid-cols-[1fr_1fr_auto]">
                  <ColumnPicker
                    fileId={link.file_a_id}
                    col={link.col_a}
                    draftFiles={draftFiles}
                    fileNameMap={fileNameMap}
                    onChange={(fileId, col) =>
                      onUpdateLink(idx, { file_a_id: fileId, col_a: col })
                    }
                  />
                  <ColumnPicker
                    fileId={link.file_b_id}
                    col={link.col_b}
                    draftFiles={draftFiles}
                    fileNameMap={fileNameMap}
                    onChange={(fileId, col) =>
                      onUpdateLink(idx, { file_b_id: fileId, col_b: col })
                    }
                  />
                  <button
                    type="button"
                    onClick={() => onRemoveLink(idx)}
                    className="small-caps text-xs text-red-600 hover:underline"
                  >
                    remove
                  </button>
                  <select
                    value={link.direction}
                    onChange={(e) =>
                      onUpdateLink(idx, { direction: e.target.value as LinkDirection })
                    }
                    className="border border-neutral-300 bg-white px-2 py-1 text-xs"
                  >
                    {DIRECTIONS.map((direction) => (
                      <option key={direction} value={direction}>
                        {direction.replace(/_/g, " ")}
                      </option>
                    ))}
                  </select>
                  <input
                    type="text"
                    value={link.summary}
                    onChange={(e) => onUpdateLink(idx, { summary: e.target.value })}
                    className="md:col-span-2 border border-neutral-300 px-2 py-1 text-xs"
                  />
                </div>
              ) : (
                <>
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <ColumnLabel
                      fileId={link.file_a_id}
                      col={link.col_a}
                      fileNameMap={fileNameMap}
                    />
                    <span className="text-ember">{"->"}</span>
                    <ColumnLabel
                      fileId={link.file_b_id}
                      col={link.col_b}
                      fileNameMap={fileNameMap}
                    />
                    <span className="small-caps border border-neutral-200 px-1.5 py-0.5 text-[10px] text-neutral-500">
                      {link.direction.replace(/_/g, " ")}
                    </span>
                  </div>
                  {link.summary ? (
                    <p className="mt-2 text-sm text-neutral-600">{link.summary}</p>
                  ) : null}
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function ColumnPicker({
  fileId,
  col,
  draftFiles,
  fileNameMap,
  onChange,
}: {
  fileId: string;
  col: string;
  draftFiles: DiscoveredFile[];
  fileNameMap: Map<string, string>;
  onChange: (fileId: string, col: string) => void;
}) {
  const file = draftFiles.find((f) => f.file_id === fileId);
  return (
    <div className="grid grid-cols-2 gap-2">
      <select
        value={fileId}
        onChange={(e) => {
          const next = draftFiles.find((f) => f.file_id === e.target.value);
          onChange(e.target.value, next?.columns[0]?.name ?? "");
        }}
        className="min-w-0 border border-neutral-300 bg-white px-2 py-1 text-xs"
      >
        {draftFiles.map((f) => (
          <option key={f.file_id} value={f.file_id}>
            {fileNameMap.get(f.file_id) ?? f.friendly_name}
          </option>
        ))}
      </select>
      <select
        value={col}
        onChange={(e) => onChange(fileId, e.target.value)}
        className="min-w-0 border border-neutral-300 bg-white px-2 py-1 text-xs"
      >
        {(file?.columns ?? []).map((c) => (
          <option key={c.column_id || c.name} value={c.name}>
            {c.name}
          </option>
        ))}
      </select>
    </div>
  );
}

function PreviewTable({ preview }: { preview: FilePreviewResponse }) {
  return (
    <div className="max-h-[30rem] overflow-auto">
      <table className="w-full min-w-max border-collapse text-xs">
        <thead className="sticky top-0 z-10 bg-neutral-50">
          <tr>
            {preview.columns.map((column) => (
              <th
                key={column}
                className="hairline border-b px-3 py-2 text-left font-mono text-xs text-ink"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {preview.rows.map((row, rowIdx) => (
            <tr key={rowIdx} className="hairline border-b hover:bg-neutral-50">
              {preview.columns.map((_, colIdx) => (
                <td
                  key={`${rowIdx}-${colIdx}`}
                  className="max-w-[18rem] truncate px-3 py-2 font-mono text-xs text-neutral-700"
                  title={formatCell(row[colIdx])}
                >
                  {formatCell(row[colIdx]) || <span className="text-neutral-300">empty</span>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="small-caps px-4 py-3 text-[10px] text-neutral-500">
        Showing {preview.rows.length} of {preview.total_rows.toLocaleString()} rows
      </div>
    </div>
  );
}

function Metric({
  label,
  value,
  text,
}: {
  label: string;
  value: number | string;
  text?: boolean;
}) {
  return (
    <div className="hairline border bg-white p-4">
      <div className="small-caps text-xs text-neutral-500">{label}</div>
      <div className={`mt-2 font-mono text-2xl text-ink ${text ? "text-lg" : ""}`}>
        {value}
      </div>
    </div>
  );
}

function ColumnLabel({
  fileId,
  col,
  fileNameMap,
}: {
  fileId: string;
  col: string;
  fileNameMap: Map<string, string>;
}) {
  return (
    <span className="font-mono text-sm text-ink">
      {fileNameMap.get(fileId) ?? fileId}.{col}
    </span>
  );
}

function groupRelationships(
  files: DiscoveredFile[],
  links: DiscoveredLink[],
): RelationshipGroup[] {
  const fileOrder = new Map(files.map((file, idx) => [file.file_id, idx]));
  const groups = new Map<string, RelationshipGroup>();

  for (const link of links) {
    const aOrder = fileOrder.get(link.file_a_id) ?? Number.MAX_SAFE_INTEGER;
    const bOrder = fileOrder.get(link.file_b_id) ?? Number.MAX_SAFE_INTEGER;
    const leftId = aOrder <= bOrder ? link.file_a_id : link.file_b_id;
    const rightId = aOrder <= bOrder ? link.file_b_id : link.file_a_id;
    const key = `${leftId}::${rightId}`;
    const current = groups.get(key);
    if (current) {
      current.links.push(link);
    } else {
      groups.set(key, { key, leftId, rightId, links: [link] });
    }
  }

  return [...groups.values()]
    .map((group) => ({
      ...group,
      links: [...group.links].sort((a, b) => a.col_a.localeCompare(b.col_a)),
    }))
    .sort((a, b) => {
      const leftDiff =
        (fileOrder.get(a.leftId) ?? 0) - (fileOrder.get(b.leftId) ?? 0);
      if (leftDiff !== 0) return leftDiff;
      return (fileOrder.get(a.rightId) ?? 0) - (fileOrder.get(b.rightId) ?? 0);
    });
}

function linkLookupKey(fileA: string, colA: string, fileB: string, colB: string): string {
  const left = `${fileA}:${colA.trim().toLowerCase()}`;
  const right = `${fileB}:${colB.trim().toLowerCase()}`;
  return [left, right].sort().join("::");
}

function friendlyEventMessage(event: ProcessingEvent): string {
  if (event.kind === "error") return event.message;
  const phase = PHASES.find((item) => item.kinds.includes(event.kind));
  return phase?.label ?? event.message;
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  return JSON.stringify(value);
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="small-caps hairline border-b px-3 py-2 text-left text-xs text-neutral-500">
      {children}
    </th>
  );
}

function Td({
  children,
  mono,
}: {
  children: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <td className={`px-3 py-2 align-top text-xs ${mono ? "font-mono" : ""}`}>
      {children}
    </td>
  );
}
