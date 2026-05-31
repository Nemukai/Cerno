import { useEffect, useMemo, useState, type ReactNode } from "react";
import { getFilePreview } from "../lib/api";
import type {
  DataDoc,
  DiscoveredColumn,
  DiscoveredFile,
  DiscoveredLink,
  DiscoveryResponse,
  FilePreviewResponse,
  FileRecord,
  Link,
  LinkDirection,
  ProcessingEvent,
  SimpleDtype,
} from "../lib/types";

type Props = {
  files: FileRecord[];
  links: Link[];
  discovery: DiscoveryResponse | null;
  doc: DataDoc | null;
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
  onDeleteFile: (fileId: string) => void;
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

const STEPS: { key: string; label: string }[] = [
  { key: "reading_files", label: "Reading files" },
  { key: "profiling_columns", label: "Profiling columns" },
  { key: "understanding_structure", label: "Understanding structure" },
  { key: "building_data_map", label: "Building data map" },
  { key: "mapping_connections", label: "Mapping connections" },
];

export function SchemaTab({
  files,
  links: storedLinks,
  discovery,
  doc,
  events,
  processing,
  approving,
  canProcess,
  onProcess,
  onApprove,
  onDeleteFile,
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
    }
  }, [discovery, editing]);

  useEffect(() => {
    if (!activeFileId) {
      setPreview(null);
      setPreviewError(null);
      setPreviewLoading(false);
      return;
    }
    setPreview(null);
    setPreviewError(null);
    setPreviewLoading(true);
    getFilePreview(activeFileId, 50)
      .then((p) => setPreview(p))
      .catch((err: Error) => setPreviewError(err.message))
      .finally(() => setPreviewLoading(false));
  }, [activeFileId]);

  const fileById = useMemo(() => {
    const m = new Map<string, FileRecord>();
    for (const file of files) m.set(file.id, file);
    return m;
  }, [files]);

  const fileNameMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const f of files) m.set(f.id, f.friendly_name || cleanFilename(f.filename));
    for (const df of draftFiles) {
      const friendly = df.friendly_name?.trim();
      if (friendly) m.set(df.file_id, friendly);
    }
    return m;
  }, [files, draftFiles]);

  const status = discovery?.status ?? "empty";
  const showProcessingCheckpoints =
    events.length > 0 &&
    (processing || status === "discovering" || status === "failed");
  const summary = useMemo(
    () => buildSummary(files, draftFiles, draftLinks, status),
    [files, draftFiles, draftLinks, status],
  );
  const warnings = useMemo(
    () => buildWarnings(draftFiles, draftLinks, fileNameMap),
    [draftFiles, draftLinks, fileNameMap],
  );
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

  const activeFile =
    draftFiles.find((file) => file.file_id === activeFileId) ?? null;
  const activeFileIdx = activeFile
    ? draftFiles.findIndex((file) => file.file_id === activeFile.file_id)
    : -1;
  const relationshipGroups = useMemo(
    () => groupRelationships(draftFiles, draftLinks),
    [draftFiles, draftLinks],
  );
  const guideSummary = useMemo(
    () => buildGuideSummary(doc, draftFiles),
    [doc, draftFiles],
  );

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

  const addLink = (preferredFileId?: string) => {
    const a = preferredFileId
      ? draftFiles.find((file) => file.file_id === preferredFileId)
      : draftFiles[0];
    const b = draftFiles.find((file) => file.file_id !== a?.file_id);
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
    setActiveFileId(a.file_id);
  };

  const removeLink = (idx: number) => {
    setDraftLinks((prev) => prev.filter((_, i) => i !== idx));
  };

  return (
    <div className="relative min-h-full overflow-hidden px-8 py-7 text-foreground">
      <div className="pointer-events-none absolute inset-0 opacity-80" aria-hidden="true">
        <div className="absolute right-[-14rem] top-[-18rem] h-[36rem] w-[48rem] rounded-full border border-primary/10" />
        <div className="absolute right-[-9rem] top-[-13rem] h-[28rem] w-[39rem] rounded-full border border-primary/14" />
        <div className="absolute right-[-4rem] top-[-8rem] h-[20rem] w-[29rem] rounded-full border border-primary/12" />
      </div>

      <header className="relative z-10 mb-7 grid gap-6 border-b border-foreground/12 pb-6 xl:grid-cols-[minmax(0,1fr)_auto]">
        <div className="max-w-4xl">
          <div className="small-caps text-xs text-primary">insights</div>
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <h2 className="font-display text-4xl leading-none tracking-tight text-foreground">
              {schemaTitle(status, processing)}
            </h2>
            <span className="small-caps border border-foreground/12 bg-card/80 px-2 py-1 text-xs text-foreground/58">
              {summary.state}
            </span>
          </div>
          <p className="mt-3 max-w-2xl text-sm leading-6 text-foreground/62">
            {headerDescription(status, processing, files.length)}
          </p>
        </div>
        <div className="flex items-start gap-2">
          {status === "empty" || status === "failed" ? (
            <button
              type="button"
              onClick={onProcess}
              disabled={!canProcess || processing}
              className="small-caps border border-foreground bg-primary px-4 py-2 text-xs text-foreground transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40"
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
                  className="small-caps border border-foreground/28 bg-muted/70 px-4 py-2 text-xs transition hover:bg-card disabled:opacity-40"
                >
                  cancel
                </button>
                <button
                  type="button"
                  onClick={handleApprove}
                  disabled={approving}
                  className="small-caps border border-foreground bg-primary px-4 py-2 text-xs text-foreground transition hover:bg-primary/90 disabled:opacity-40"
                >
                  {approving ? "saving" : "save and apply"}
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => setEditing(true)}
                  className="small-caps border border-foreground/28 bg-muted/70 px-4 py-2 text-xs transition hover:bg-card"
                >
                  edit
                </button>
                {status === "pending_review" ? (
                  <button
                    type="button"
                    onClick={handleApprove}
                    disabled={approving}
                    className="small-caps border border-foreground bg-primary px-4 py-2 text-xs text-foreground transition hover:bg-primary/90 disabled:opacity-40"
                  >
                    {approving ? "approving" : "approve"}
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={onProcess}
                    disabled={processing}
                    className="small-caps border border-foreground/28 bg-muted/70 px-4 py-2 text-xs transition hover:bg-card disabled:opacity-40"
                  >
                    re-process
                  </button>
                )}
              </>
            )
          ) : null}
        </div>
      </header>

      {showProcessingCheckpoints ? (
        <ProcessingCheckpoints events={events} processing={processing} />
      ) : null}

      {status === "empty" && !processing ? (
        <EmptyState canProcess={canProcess} onProcess={onProcess} />
      ) : null}

      {discovery && (status === "pending_review" || status === "approved") ? (
        <div className="relative z-10 grid gap-7">
          <section className="grid gap-6 border-b border-foreground/12 pb-7 xl:grid-cols-[minmax(0,1.08fr)_minmax(28rem,0.92fr)]">
            <div className="min-w-0">
              <div className="small-caps text-xs text-foreground/42">summary</div>
              {editing ? (
                <textarea
                  value={draftOverview}
                  onChange={(e) => setDraftOverview(e.target.value)}
                  rows={5}
                  className="mt-3 w-full border border-foreground/18 bg-card/84 p-3 text-sm leading-6 focus:outline-none focus:ring-1 focus:ring-foreground"
                />
              ) : (
                <p className="mt-3 max-w-3xl text-lg leading-8 text-foreground/72">
                  {shortText(draftOverview || "No overview was generated.", 310)}
                </p>
              )}
              {warnings.total > 0 ? (
                <button
                  type="button"
                  onClick={() => {
                    const first = draftFiles.find((file) =>
                      warnings.byFile.has(file.file_id),
                    );
                    if (first) setActiveFileId(first.file_id);
                  }}
                  className="mt-5 border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-left text-xs text-amber-600 transition hover:bg-amber-500/20"
                >
                  {warnings.total} item{warnings.total === 1 ? "" : "s"} need review.
                  Open the marked document for detail.
                </button>
              ) : (
                <div className="mt-5 inline-flex border border-primary/18 bg-primary/18 px-3 py-2 text-xs text-primary">
                  No document warnings found.
                </div>
              )}
            </div>
            <div className="grid grid-cols-2 gap-x-8 gap-y-5 border border-foreground/12 bg-card/70 p-5 shadow-[0_18px_60px_rgba(20,18,16,0.04)] sm:grid-cols-3">
              <Stat label="rows" value={summary.rows} />
              <Stat label="files" value={summary.files} />
              <Stat label="fields" value={summary.columns} />
              <Stat label="connections" value={summary.relationships} />
              <Stat label="size" value={summary.size} />
              <Stat label="state" value={summary.state} />
            </div>
          </section>

          <DocumentSummaryList
            files={draftFiles}
            fileById={fileById}
            fileNameMap={fileNameMap}
            warnings={warnings}
            onSelectFile={setActiveFileId}
          />

          <ConnectionSummaryList
            groups={relationshipGroups}
            fileNameMap={fileNameMap}
            linkStats={linkStats}
            onSelectFile={setActiveFileId}
            editing={editing}
            onAddLink={addLink}
            canAddLink={draftFiles.length >= 2}
          />

          <AgentContextSummary
            summary={guideSummary}
            files={draftFiles}
            onSelectFile={setActiveFileId}
          />
        </div>
      ) : null}

      <FileSchemaDrawer
        open={Boolean(activeFile)}
        file={activeFile}
        fileIdx={activeFileIdx}
        record={activeFile ? fileById.get(activeFile.file_id) : undefined}
        editing={editing}
        preview={preview}
        previewLoading={previewLoading}
        previewError={previewError}
        links={draftLinks}
        linkStats={linkStats}
        draftFiles={draftFiles}
        fileNameMap={fileNameMap}
        warnings={activeFile ? warnings.byFile.get(activeFile.file_id) ?? [] : []}
        onClose={() => setActiveFileId(null)}
        onUpdateFile={updateFile}
        onUpdateColumn={updateColumn}
        onAddColumn={addColumn}
        onRemoveColumn={removeColumn}
        onAddLink={addLink}
        onUpdateLink={updateLink}
        onRemoveLink={removeLink}
        onDeleteFile={onDeleteFile}
      />
    </div>
  );
}

function schemaTitle(status: string, processing: boolean): string {
  if (processing || status === "discovering") return "Understanding your files";
  if (status === "pending_review") return "Review the data map";
  if (status === "approved") return "Approved data map";
  if (status === "failed") return "Processing needs attention";
  return "No data map yet";
}

function headerDescription(
  status: string,
  processing: boolean,
  fileCount: number,
): string {
  if (processing || status === "discovering") {
    return "Cerno is reading the uploads, finding the real headers, and looking for connections across documents.";
  }
  if (status === "pending_review") {
    return "Check the plain-language map before Cerno answers questions from these files.";
  }
  if (status === "approved") {
    return "This map is the shared understanding Cerno uses for chat.";
  }
  if (status === "failed") {
    return "Processing stopped before Cerno could finish the map. Fix the issue, then process the files again.";
  }
  if (fileCount > 0) {
    return "Process the uploaded files to find headers, meanings, and connections between documents.";
  }
  return "Upload one or more files, then Cerno can build a plain-language map of the dataset.";
}

function EmptyState({
  canProcess,
  onProcess,
}: {
  canProcess: boolean;
  onProcess: () => void;
}) {
  return (
    <div className="mt-10 max-w-2xl border border-border bg-card p-5">
      <div className="flex flex-wrap items-start justify-between gap-5">
        <div className="min-w-0 flex-1">
          <h3 className="font-mono text-base text-foreground">Start with uploaded documents</h3>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            {canProcess
              ? "Cerno will identify headers, explain fields, and look for connections between files."
              : "Upload one or more files first, then Cerno can build the data map."}
          </p>
        </div>
        {canProcess ? (
          <button
            type="button"
            onClick={onProcess}
            className="small-caps shrink-0 border border-foreground bg-primary px-4 py-2 text-sm text-primary-foreground hover:bg-primary/90"
          >
            process files
          </button>
        ) : null}
      </div>
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
  const isDone = events.some((e) => e.kind === "done");
  const progress = events.at(-1)?.progress ?? 0;

  // Build a set of step_keys that have appeared
  const seenKeys = new Set(events.map((e) => e.step_key).filter(Boolean));
  // Find the timestamp of first event for each step_key
  const stepTimestamps: Record<string, { start: string; end?: string }> = {};
  for (const ev of events) {
    if (!ev.step_key) continue;
    if (!stepTimestamps[ev.step_key]) {
      stepTimestamps[ev.step_key] = { start: ev.created_at };
    }
  }
  // Mark the end of a step when the next step starts
  const stepOrder = STEPS.map((s) => s.key);
  for (let i = 0; i < stepOrder.length - 1; i++) {
    const current = stepOrder[i]!;
    const next = stepOrder[i + 1]!;
    if (stepTimestamps[current] && stepTimestamps[next]) {
      stepTimestamps[current]!.end = stepTimestamps[next]!.start;
    }
  }
  if (isDone && stepTimestamps["mapping_connections"]) {
    const doneEv = events.find((e) => e.kind === "done");
    if (doneEv) stepTimestamps["mapping_connections"]!.end = doneEv.created_at;
  }

  // Find the last step that has been seen
  let activeStepIdx = -1;
  for (let i = STEPS.length - 1; i >= 0; i--) {
    if (seenKeys.has(STEPS[i]!.key)) {
      activeStepIdx = i;
      break;
    }
  }

  return (
    <section className="mb-6 border border-border bg-card p-5">
      <div className="flex items-center justify-between gap-4 mb-4">
        <h3 className="font-mono text-sm text-foreground">
          {latestError ? "Processing stopped" : isDone ? "Processing complete" : "Analyzing your data"}
        </h3>
        <div className="small-caps text-xs text-muted-foreground/70">
          {isDone ? "complete" : `${progress}%`}
        </div>
      </div>

      {/* Progress bar */}
      <div className="relative h-1.5 overflow-hidden bg-muted mb-5">
        <div
          className={`h-full transition-all duration-700 ease-out ${isDone ? "bg-emerald-500" : "bg-primary"}`}
          style={{ width: `${progress}%` }}
        />
        {processing && !isDone && !latestError ? (
          <div className="absolute inset-y-0 left-0 w-1/3 animate-pulse bg-primary/30" />
        ) : null}
      </div>

      {/* Steps */}
      <div className="grid gap-1">
        {STEPS.map((step, idx) => {
          const isComplete = isDone || idx < (isDone ? STEPS.length : activeStepIdx);
          const isActive = !isDone && idx === activeStepIdx && !latestError;
          const ts = stepTimestamps[step.key];
          const elapsed =
            isComplete && ts?.start && ts?.end ? formatElapsed(ts.start, ts.end) : null;

          return (
            <div
              key={step.key}
              className={`flex items-center gap-3 px-3 py-2 transition-colors ${
                isActive ? "bg-primary/10" : ""
              }`}
            >
              {/* Icon */}
              <div className="flex-shrink-0 w-5 h-5 flex items-center justify-center">
                {isComplete ? (
                  <svg className="w-4 h-4 text-emerald-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="square" strokeLinejoin="miter" d="M5 13l4 4L19 7" />
                  </svg>
                ) : isActive ? (
                  <div className="w-3.5 h-3.5 border-2 border-primary border-t-transparent animate-spin" />
                ) : (
                  <div className="w-2 h-2 bg-muted-foreground/30" />
                )}
              </div>

              {/* Label */}
              <span
                className={`text-sm flex-1 ${
                  isComplete
                    ? "text-muted-foreground"
                    : isActive
                      ? "text-foreground font-medium"
                      : "text-muted-foreground/50"
                }`}
              >
                {step.label}
              </span>

              {/* Elapsed time */}
              {elapsed ? (
                <span className="text-[10px] font-mono text-muted-foreground/70">
                  {elapsed}
                </span>
              ) : isActive ? (
                <span className="text-[10px] font-mono text-primary animate-pulse">
                  working
                </span>
              ) : null}
            </div>
          );
        })}
      </div>

      {/* Latest message */}
      {!latestError && events.length > 0 && !isDone ? (
        <p className="mt-3 px-3 text-xs text-muted-foreground/70 animate-pulse">
          {events.at(-1)?.message}
        </p>
      ) : null}

      {latestError ? (
        <div className="mt-4 border border-destructive/40 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {friendlyEventMessage(latestError)}
        </div>
      ) : null}
    </section>
  );
}

function formatElapsed(start: string, end: string): string {
  const ms = new Date(end).getTime() - new Date(start).getTime();
  if (ms < 0) return "";
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  const secs = seconds % 60;
  return `${minutes}m ${secs}s`;
}

type SummaryStats = {
  size: string;
  rows: string;
  files: string;
  columns: string;
  relationships: string;
  state: string;
};

type WarningState = {
  total: number;
  byFile: Map<string, string[]>;
};

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="small-caps text-[11px] text-foreground/44">{label}</div>
      <div className="mt-1 font-mono text-lg text-foreground">{value}</div>
    </div>
  );
}

function DocumentSummaryList({
  files,
  fileById,
  fileNameMap,
  warnings,
  onSelectFile,
}: {
  files: DiscoveredFile[];
  fileById: Map<string, FileRecord>;
  fileNameMap: Map<string, string>;
  warnings: WarningState;
  onSelectFile: (fileId: string) => void;
}) {
  return (
    <section className="border-b border-foreground/12 pb-7">
      <div className="mb-4 flex items-end justify-between gap-4">
        <div>
          <div className="small-caps text-xs text-primary">documents</div>
          <h3 className="mt-1 font-mono text-xl text-foreground">Files Cerno can use</h3>
        </div>
        <div className="text-xs text-foreground/46">Click a file for fields and rows.</div>
      </div>
      <div className="divide-y divide-foreground/10 border-y border-foreground/12">
        {files.map((file) => {
          const record = fileById.get(file.file_id);
          const fileWarnings = warnings.byFile.get(file.file_id) ?? [];
          return (
            <button
              key={file.file_id}
              type="button"
              onClick={() => onSelectFile(file.file_id)}
              className="group grid w-full gap-3 px-3 py-4 text-left transition hover:bg-card/80 lg:grid-cols-[minmax(14rem,0.7fr)_minmax(0,1fr)_auto]"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span
                    className={`h-2.5 w-2.5 ${fileWarnings.length > 0 ? "bg-primary" : "bg-primary"}`}
                  />
                  <span className="truncate font-mono text-base text-foreground">
                    {fileNameMap.get(file.file_id) ?? file.friendly_name}
                  </span>
                </div>
                <div className="mt-1 truncate text-xs text-foreground/45">
                  {record?.filename ?? "Original file unknown"}
                </div>
              </div>
              <div className="min-w-0 text-sm leading-6 text-foreground/62">
                {shortText(file.description || "No description yet.", 150)}
              </div>
              <div className="flex items-center gap-5 font-mono text-xs text-foreground/56 lg:justify-end">
                <span>{formatNumber(record?.row_count ?? 0)} rows</span>
                <span>{file.columns.length} fields</span>
                <span
                  className={`small-caps border px-2 py-1 font-sans text-[10px] ${
                    fileWarnings.length > 0
                      ? "border-amber-500/40 bg-amber-500/10 text-amber-600"
                      : "border-foreground/12 bg-muted/70 text-foreground/48"
                  }`}
                >
                  {fileWarnings.length > 0 ? "review" : "ready"}
                </span>
              </div>
            </button>
          );
        })}
      </div>
    </section>
  );
}

function ConnectionSummaryList({
  groups,
  fileNameMap,
  linkStats,
  onSelectFile,
  editing,
  onAddLink,
  canAddLink,
}: {
  groups: RelationshipGroup[];
  fileNameMap: Map<string, string>;
  linkStats: Map<string, LinkStat>;
  onSelectFile: (fileId: string) => void;
  editing: boolean;
  onAddLink: () => void;
  canAddLink: boolean;
}) {
  const visibleGroups = [...groups]
    .sort((a, b) => b.links.length - a.links.length)
    .slice(0, 6);
  const hiddenCount = Math.max(0, groups.length - visibleGroups.length);

  return (
    <section className="border-b border-foreground/12 pb-7">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="small-caps text-xs text-primary">connections</div>
          <h3 className="mt-1 font-mono text-xl text-foreground">How files relate</h3>
        </div>
        {editing ? (
          <button
            type="button"
            onClick={onAddLink}
            disabled={!canAddLink}
            className="small-caps border border-foreground/28 bg-muted/70 px-3 py-2 text-xs transition hover:bg-card disabled:opacity-40"
          >
            add connection
          </button>
        ) : null}
      </div>
      {visibleGroups.length === 0 ? (
        <div className="border border-foreground/12 bg-card/62 p-4 text-sm text-foreground/58">
          No strong file connections found yet.
        </div>
      ) : (
        <div className="grid gap-2">
          {visibleGroups.map((group) => {
            const weakCount = group.links.filter((link) =>
              isWeakLink(
                linkStats.get(
                  linkLookupKey(link.file_a_id, link.col_a, link.file_b_id, link.col_b),
                ),
              ),
            ).length;
            return (
              <div
                key={group.key}
                className="grid items-center gap-3 border border-foreground/10 bg-card/58 px-3 py-3 md:grid-cols-[minmax(0,1fr)_auto_minmax(0,1fr)_auto]"
              >
                <button
                  type="button"
                  onClick={() => onSelectFile(group.leftId)}
                  className="truncate border border-foreground/10 bg-muted/62 px-3 py-2 text-left font-mono text-sm transition hover:border-primary/40"
                >
                  {fileNameMap.get(group.leftId) ?? group.leftId}
                </button>
                <span className="small-caps whitespace-nowrap border border-primary/22 bg-primary/8 px-2 py-1 text-[10px] text-primary">
                  {group.links.length} link{group.links.length === 1 ? "" : "s"}
                </span>
                <button
                  type="button"
                  onClick={() => onSelectFile(group.rightId)}
                  className="truncate border border-foreground/10 bg-muted/62 px-3 py-2 text-left font-mono text-sm transition hover:border-primary/40"
                >
                  {fileNameMap.get(group.rightId) ?? group.rightId}
                </button>
                <button
                  type="button"
                  onClick={() => onSelectFile(group.leftId)}
                  className="small-caps text-[10px] text-foreground/44 transition hover:text-primary"
                >
                  inspect
                </button>
                {weakCount > 0 ? (
                  <div className="text-xs text-amber-600 md:col-span-4">
                    {weakCount} weak link{weakCount === 1 ? "" : "s"}.
                  </div>
                ) : null}
              </div>
            );
          })}
          {hiddenCount > 0 ? (
            <div className="small-caps px-3 py-2 text-xs text-foreground/42">
              {hiddenCount} more connection group{hiddenCount === 1 ? "" : "s"} hidden from this view.
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

type GuideSummary = {
  documented: number;
  notes: number;
  glossary: number;
  questions: number;
  caveats: number;
};

function AgentContextSummary({
  summary,
  files,
  onSelectFile,
}: {
  summary: GuideSummary;
  files: DiscoveredFile[];
  onSelectFile: (fileId: string) => void;
}) {
  return (
    <section className="pb-3">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="small-caps text-xs text-primary">context</div>
          <h3 className="mt-1 font-mono text-xl text-foreground">Saved for chat</h3>
        </div>
        {files[0] ? (
          <button
            type="button"
            onClick={() => onSelectFile(files[0]!.file_id)}
            className="small-caps border border-foreground/16 bg-muted/70 px-3 py-2 text-xs transition hover:bg-card"
          >
            inspect details
          </button>
        ) : null}
      </div>
      <div className="grid gap-3 md:grid-cols-5">
        <ContextCount label="documents" value={summary.documented} />
        <ContextCount label="notes" value={summary.notes} />
        <ContextCount label="glossary" value={summary.glossary} />
        <ContextCount label="questions" value={summary.questions} />
        <ContextCount label="watch-outs" value={summary.caveats} />
      </div>
    </section>
  );
}

function ContextCount({ label, value }: { label: string; value: number }) {
  return (
    <div className="border border-foreground/10 bg-card/52 px-3 py-3">
      <div className="font-mono text-xl text-foreground">{formatNumber(value)}</div>
      <div className="small-caps mt-1 text-[10px] text-foreground/42">{label}</div>
    </div>
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

function FileSchemaDrawer({
  open,
  file,
  fileIdx,
  record,
  editing,
  preview,
  previewLoading,
  previewError,
  links,
  linkStats,
  draftFiles,
  fileNameMap,
  warnings,
  onClose,
  onUpdateFile,
  onUpdateColumn,
  onAddColumn,
  onRemoveColumn,
  onAddLink,
  onUpdateLink,
  onRemoveLink,
  onDeleteFile,
}: {
  open: boolean;
  file: DiscoveredFile | null;
  fileIdx: number;
  record?: FileRecord;
  editing: boolean;
  preview: FilePreviewResponse | null;
  previewLoading: boolean;
  previewError: string | null;
  links: DiscoveredLink[];
  linkStats: Map<string, LinkStat>;
  draftFiles: DiscoveredFile[];
  fileNameMap: Map<string, string>;
  warnings: string[];
  onClose: () => void;
  onUpdateFile: (idx: number, patch: Partial<DiscoveredFile>) => void;
  onUpdateColumn: (
    fileIdx: number,
    colIdx: number,
    patch: Partial<DiscoveredColumn>,
  ) => void;
  onAddColumn: (fileIdx: number) => void;
  onRemoveColumn: (fileIdx: number, colIdx: number) => void;
  onAddLink: (preferredFileId?: string) => void;
  onUpdateLink: (idx: number, patch: Partial<DiscoveredLink>) => void;
  onRemoveLink: (idx: number) => void;
  onDeleteFile: (fileId: string) => void;
}) {
  if (!open || !file || fileIdx < 0) return null;
  const fileLinks = links
    .map((link, idx) => ({ link, idx }))
    .filter(({ link }) => link.file_a_id === file.file_id || link.file_b_id === file.file_id);

  return (
    <div className="fixed inset-0 z-50 bg-black/20" onClick={onClose}>
      <aside
        className="absolute inset-y-0 right-0 flex w-full max-w-3xl flex-col border-l border-foreground bg-card shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-border flex items-start justify-between gap-4 border-b p-5">
          <div className="min-w-0">
            <div className="small-caps text-xs text-muted-foreground">document detail</div>
            {editing ? (
              <input
                type="text"
                value={file.friendly_name}
                onChange={(e) => onUpdateFile(fileIdx, { friendly_name: e.target.value })}
                className="mt-2 w-full border border-border bg-card px-2 py-1 font-mono text-xl focus:outline-none focus:ring-1 focus:ring-ring"
              />
            ) : (
              <h3 className="mt-1 truncate font-mono text-xl text-foreground">
                {file.friendly_name}
              </h3>
            )}
            <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
              <span>{record?.filename ?? "Original file unknown"}</span>
              <span>{formatSize(record?.original_size_bytes)}</span>
              <span>{formatNumber(record?.row_count ?? 0)} rows</span>
              <span>{file.columns.length} columns</span>
              <span>header starts on row {file.header_row}</span>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {record ? (
              <button
                type="button"
                onClick={() => {
                  const ok = window.confirm(
                    `Remove ${record.filename}? Cerno will refresh the workspace understanding after deletion.`,
                  );
                  if (!ok) return;
                  onClose();
                  onDeleteFile(record.id);
                }}
                className="small-caps border border-destructive/40 px-2 py-1 text-xs text-destructive hover:bg-destructive/10"
              >
                delete file
              </button>
            ) : null}
            <button
              type="button"
              onClick={onClose}
              className="small-caps border border-foreground px-2 py-1 text-xs hover:bg-muted"
            >
              close
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-5">
          {warnings.length > 0 ? (
            <div className="border border-amber-500/40 bg-amber-500/10 p-3 text-xs text-amber-600">
              {warnings.map((warning) => (
                <div key={warning}>{warning}</div>
              ))}
            </div>
          ) : null}

          <section>
            <div className="small-caps text-xs text-muted-foreground">what this contains</div>
            {editing ? (
              <textarea
                value={file.description}
                onChange={(e) => onUpdateFile(fileIdx, { description: e.target.value })}
                rows={3}
                className="mt-2 w-full border border-border bg-card p-2 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              />
            ) : (
              <p className="mt-2 text-sm leading-6 text-foreground/80">
                {file.description || "No description yet."}
              </p>
            )}
            {editing ? (
              <label className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
                Header starts on row
                <input
                  type="number"
                  min={0}
                  value={file.header_row}
                  onChange={(e) =>
                    onUpdateFile(fileIdx, {
                      header_row: Math.max(0, Number(e.target.value) || 0),
                    })
                  }
                  className="w-20 border border-border bg-card px-2 py-1"
                />
              </label>
            ) : null}
          </section>

          <section>
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <div className="small-caps text-xs text-muted-foreground">columns</div>
                <h4 className="mt-1 font-mono text-base text-foreground">Important fields</h4>
              </div>
              {editing ? (
                <button
                  type="button"
                  onClick={() => onAddColumn(fileIdx)}
                  className="small-caps border border-foreground px-2 py-1 text-xs hover:bg-muted"
                >
                  add column
                </button>
              ) : null}
            </div>
            <SchemaColumnTable
              file={file}
              fileIdx={fileIdx}
              editing={editing}
              onUpdateColumn={onUpdateColumn}
              onRemoveColumn={onRemoveColumn}
            />
          </section>

          <section>
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <div className="small-caps text-xs text-muted-foreground">connections</div>
                <h4 className="mt-1 font-mono text-base text-foreground">Related documents</h4>
              </div>
              {editing ? (
                <button
                  type="button"
                  onClick={() => onAddLink(file.file_id)}
                  disabled={draftFiles.length < 2}
                  className="small-caps border border-foreground px-2 py-1 text-xs hover:bg-muted disabled:opacity-40"
                >
              add connection
                </button>
              ) : null}
            </div>
            <RelationshipList
              links={fileLinks}
              linkStats={linkStats}
              draftFiles={draftFiles}
              fileNameMap={fileNameMap}
              editing={editing}
              onUpdateLink={onUpdateLink}
              onRemoveLink={onRemoveLink}
            />
          </section>

          <section>
            <div className="border-border flex items-center justify-between border-b pb-3">
              <div>
                <div className="small-caps text-xs text-muted-foreground">preview</div>
                <h4 className="mt-1 font-mono text-base text-foreground">Sample rows</h4>
              </div>
              {preview ? (
                <div className="small-caps text-xs text-muted-foreground">
                  {formatNumber(preview.total_rows)} rows
                </div>
              ) : null}
            </div>
            {previewLoading ? (
              <div className="py-4 text-sm text-muted-foreground">Loading preview...</div>
            ) : previewError ? (
              <div className="py-4 text-sm text-destructive">{previewError}</div>
            ) : preview ? (
              <PreviewTable preview={preview} />
            ) : null}
          </section>
        </div>
      </aside>
    </div>
  );
}

function SchemaColumnTable({
  file,
  fileIdx,
  editing,
  onUpdateColumn,
  onRemoveColumn,
}: {
  file: DiscoveredFile;
  fileIdx: number;
  editing: boolean;
  onUpdateColumn: (
    fileIdx: number,
    colIdx: number,
    patch: Partial<DiscoveredColumn>,
  ) => void;
  onRemoveColumn: (fileIdx: number, colIdx: number) => void;
}) {
  return (
    <div className="overflow-x-auto border border-border bg-card">
      <table className="w-full min-w-[42rem] border-collapse text-sm">
        <thead className="bg-muted">
          <tr>
            <Th>field</Th>
            <Th>meaning</Th>
            <Th>type</Th>
            {editing ? <Th>{""}</Th> : null}
          </tr>
        </thead>
        <tbody>
          {file.columns.map((col, colIdx) => (
            <tr key={`${col.column_id}-${colIdx}`} className="border-border border-b last:border-b-0">
              <Td mono>
                {editing ? (
                  <input
                    type="text"
                    value={col.name}
                    onChange={(e) =>
                      onUpdateColumn(fileIdx, colIdx, { name: e.target.value })
                    }
                    className="w-full border border-border px-2 py-1 font-mono text-xs focus:outline-none focus:ring-1 focus:ring-ring"
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
                    className="w-full border border-border px-2 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-ring"
                  />
                ) : (
                  col.description || <span className="text-amber-600">Needs description</span>
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
                    className="border border-border bg-card px-2 py-1 text-xs"
                  >
                    {DTYPES.map((dtype) => (
                      <option key={dtype} value={dtype}>
                        {dtype}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="small-caps text-xs text-muted-foreground">{col.dtype}</span>
                )}
              </Td>
              {editing ? (
                <Td>
                  <button
                    type="button"
                    onClick={() => onRemoveColumn(fileIdx, colIdx)}
                    className="small-caps text-xs text-destructive hover:underline"
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
  );
}

function RelationshipList({
  links,
  linkStats,
  draftFiles,
  fileNameMap,
  editing,
  onUpdateLink,
  onRemoveLink,
}: {
  links: { link: DiscoveredLink; idx: number }[];
  linkStats: Map<string, LinkStat>;
  draftFiles: DiscoveredFile[];
  fileNameMap: Map<string, string>;
  editing: boolean;
  onUpdateLink: (idx: number, patch: Partial<DiscoveredLink>) => void;
  onRemoveLink: (idx: number) => void;
}) {
  if (links.length === 0) {
    return (
      <div className="border border-border bg-card p-4 text-sm text-muted-foreground">
        No connections for this document.
      </div>
    );
  }

  return (
    <div className="divide-y divide-border/60 border border-border bg-card">
      {links.map(({ link, idx }) => {
        const stat = linkStats.get(
          linkLookupKey(link.file_a_id, link.col_a, link.file_b_id, link.col_b),
        );
        return (
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
                  className="small-caps text-xs text-destructive hover:underline"
                >
                  remove
                </button>
                <select
                  value={link.direction}
                  onChange={(e) =>
                    onUpdateLink(idx, { direction: e.target.value as LinkDirection })
                  }
                  className="border border-border bg-card px-2 py-1 text-xs"
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
                  className="border border-border px-2 py-1 text-xs md:col-span-2"
                />
              </div>
            ) : (
              <>
                <div className="text-sm text-foreground">
                  {relationshipSentence(link, fileNameMap)}
                </div>
                <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
                  <span className="small-caps border border-border px-1.5 py-0.5 text-[10px]">
                    {link.direction.replace(/_/g, " ")}
                  </span>
                  <EvidenceLabel stat={stat} />
                </div>
                {link.summary ? (
                  <p className="mt-2 text-sm leading-5 text-muted-foreground">{link.summary}</p>
                ) : null}
              </>
            )}
          </div>
        );
      })}
    </div>
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
        className="min-w-0 border border-border bg-card px-2 py-1 text-xs"
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
        className="min-w-0 border border-border bg-card px-2 py-1 text-xs"
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
    <div className="max-h-[22rem] overflow-auto border border-border border-t-0 bg-card">
      <table className="w-full min-w-max border-collapse text-xs">
        <thead className="sticky top-0 z-10 bg-muted">
          <tr>
            {preview.columns.map((column) => (
              <th
                key={column}
                className="border-border border-b px-3 py-2 text-left font-mono text-xs text-foreground"
              >
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {preview.rows.map((row, rowIdx) => (
            <tr key={rowIdx} className="border-border border-b hover:bg-muted">
              {preview.columns.map((_, colIdx) => (
                <td
                  key={`${rowIdx}-${colIdx}`}
                  className="max-w-[18rem] truncate px-3 py-2 font-mono text-xs text-foreground/80"
                  title={formatCell(row[colIdx])}
                >
                  {formatCell(row[colIdx]) || <span className="text-muted-foreground/50">empty</span>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="small-caps px-4 py-3 text-[10px] text-muted-foreground">
        Showing {preview.rows.length} of {formatNumber(preview.total_rows)} rows
      </div>
    </div>
  );
}

function EvidenceLabel({ stat }: { stat?: LinkStat }) {
  const score = stat?.score ?? stat?.overlap;
  if (score === undefined) {
    return <span>semantic match</span>;
  }
  const pct = Math.max(0, Math.min(100, Math.round(score * 100)));
  const source = stat?.source === "user_added" ? "manual" : "value scan";
  return (
    <span className={pct < 70 ? "text-amber-600" : ""}>
      {pct}% match from {source}
    </span>
  );
}

function buildGuideSummary(doc: DataDoc | null, files: DiscoveredFile[]): GuideSummary {
  if (!doc) {
    return {
      documented: files.length,
      notes: 0,
      glossary: 0,
      questions: 0,
      caveats: 0,
    };
  }

  return {
    documented: doc.files.length,
    notes: doc.usage_notes.length,
    glossary: doc.glossary.length,
    questions: doc.starter_questions.length,
    caveats: doc.files.reduce((sum, file) => sum + file.caveats.length, 0),
  };
}

function buildSummary(
  files: FileRecord[],
  draftFiles: DiscoveredFile[],
  draftLinks: DiscoveredLink[],
  status: string,
): SummaryStats {
  const knownSizes = files
    .map((file) => file.original_size_bytes)
    .filter((value): value is number => typeof value === "number");
  const hasUnknownSize = knownSizes.length !== files.length;
  const totalSize = knownSizes.reduce((sum, value) => sum + value, 0);
  const totalRows = files.reduce((sum, file) => sum + file.row_count, 0);
  const columnCount = draftFiles.reduce((sum, file) => sum + file.columns.length, 0);
  return {
    size:
      knownSizes.length === 0
        ? "Unknown"
        : `${formatSize(totalSize)}${hasUnknownSize ? " +" : ""}`,
    rows: formatNumber(totalRows),
    files: formatNumber(draftFiles.length || files.length),
    columns: formatNumber(columnCount),
    relationships: formatNumber(draftLinks.length),
    state: status === "approved" ? "Approved" : "Draft",
  };
}

function buildWarnings(
  files: DiscoveredFile[],
  links: DiscoveredLink[],
  fileNameMap: Map<string, string>,
): WarningState {
  const byFile = new Map<string, string[]>();
  const add = (fileId: string, message: string) => {
    byFile.set(fileId, [...(byFile.get(fileId) ?? []), message]);
  };

  for (const file of files) {
    if (!file.description.trim()) {
      add(file.file_id, `${fileNameMap.get(file.file_id) ?? file.friendly_name} needs a description.`);
    }
    if (file.columns.length === 0) {
      add(file.file_id, "No columns were detected.");
    }
    if (file.columns.some((column) => !column.description.trim())) {
      add(file.file_id, "Some columns need plain-language meanings.");
    }
    if (file.header_row > 5) {
      add(file.file_id, `Header row ${file.header_row} is unusually deep.`);
    }
  }

  const linkedFiles = new Set<string>();
  for (const link of links) {
    linkedFiles.add(link.file_a_id);
    linkedFiles.add(link.file_b_id);
  }
  if (files.length > 1) {
    for (const file of files) {
      if (!linkedFiles.has(file.file_id)) {
        add(file.file_id, "No connection found for this document.");
      }
    }
  }

  return {
    byFile,
    total: [...byFile.values()].reduce((sum, items) => sum + items.length, 0),
  };
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

  return [...groups.values()].sort((a, b) => {
    const leftDiff =
      (fileOrder.get(a.leftId) ?? 0) - (fileOrder.get(b.leftId) ?? 0);
    if (leftDiff !== 0) return leftDiff;
    return (fileOrder.get(a.rightId) ?? 0) - (fileOrder.get(b.rightId) ?? 0);
  });
}

function relationshipSentence(
  link: DiscoveredLink,
  fileNameMap: Map<string, string>,
): string {
  const left = fileNameMap.get(link.file_a_id) ?? link.file_a_id;
  const right = fileNameMap.get(link.file_b_id) ?? link.file_b_id;
  const direction = link.direction.replace(/_/g, " ");
  return `${left} connects to ${right} through ${link.col_a} and ${link.col_b}. This looks like ${direction}.`;
}

function cleanFilename(filename: string): string {
  const stem = filename.replace(/\.[^.]+$/, "");
  return stem.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim() || filename;
}

function shortText(value: string, maxLength: number): string {
  const normalized = value.replace(/\s+/g, " ").trim();
  if (normalized.length <= maxLength) return normalized;
  const sentenceEnd = normalized.slice(0, maxLength).lastIndexOf(".");
  const cutAt = sentenceEnd > maxLength * 0.45 ? sentenceEnd + 1 : maxLength;
  return `${normalized.slice(0, cutAt).trim()}...`;
}

function formatSize(bytes: number | null | undefined): string {
  if (typeof bytes !== "number") return "Unknown size";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = units[0]!;
  for (let i = 1; i < units.length && value >= 1024; i += 1) {
    value /= 1024;
    unit = units[i]!;
  }
  return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)} ${unit}`;
}

function formatNumber(value: number): string {
  return new Intl.NumberFormat().format(value);
}

function isWeakLink(stat?: LinkStat): boolean {
  const score = stat?.score ?? stat?.overlap;
  return typeof score === "number" && score < 0.7;
}

function linkLookupKey(fileA: string, colA: string, fileB: string, colB: string): string {
  const left = `${fileA}:${colA.trim().toLowerCase()}`;
  const right = `${fileB}:${colB.trim().toLowerCase()}`;
  return [left, right].sort().join("::");
}

function friendlyEventMessage(event: ProcessingEvent): string {
  if (event.kind === "error") return event.message;
  const step = STEPS.find((s) => s.key === event.step_key);
  return step?.label ?? event.message;
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  if (typeof value === "boolean") return value ? "true" : "false";
  return JSON.stringify(value);
}

function Th({ children }: { children: ReactNode }) {
  return (
    <th className="small-caps border-border border-b px-3 py-2 text-left text-xs text-muted-foreground">
      {children}
    </th>
  );
}

function Td({
  children,
  mono,
}: {
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <td className={`px-3 py-2 align-top text-xs ${mono ? "font-mono" : ""}`}>
      {children}
    </td>
  );
}
