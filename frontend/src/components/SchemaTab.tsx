import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { EChart } from "./EChart";
import { useTheme } from "@/lib/theme";
import {
  applySchemaCorrection,
  getFilePreview,
  interpretSchemaCorrection,
} from "../lib/api";
import {
  formatNumber as formatNumberValue,
  type NumberSystem,
} from "../lib/format-number";
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
  SchemaCorrectionApplyResponse,
  SchemaCorrectionOperation,
  SchemaCorrectionPatch,
  SimpleDtype,
} from "../lib/types";

const CONFIDENCE_REVIEW_THRESHOLD = 0.8;

type Props = {
  sessionId: string;
  files: FileRecord[];
  links: Link[];
  discovery: DiscoveryResponse | null;
  doc: DataDoc | null;
  numberSystem: NumberSystem;
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
  onCorrectionApplied: (
    response: SchemaCorrectionApplyResponse,
  ) => Promise<void> | void;
  onCorrectionError: (message: string) => void;
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
  sessionId,
  files,
  links: storedLinks,
  discovery,
  doc,
  numberSystem,
  events,
  processing,
  approving,
  canProcess,
  onProcess,
  onApprove,
  onCorrectionApplied,
  onCorrectionError,
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
    () => buildSummary(files, draftFiles, draftLinks, status, numberSystem),
    [files, draftFiles, draftLinks, status, numberSystem],
  );
  const narrative = useMemo(
    () =>
      buildNarrative(
        draftOverview,
        draftFiles,
        draftLinks,
        fileNameMap,
        numberSystem,
      ),
    [draftOverview, draftFiles, draftLinks, fileNameMap, numberSystem],
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

  const handleCorrectionApplied = async (
    response: SchemaCorrectionApplyResponse,
  ) => {
    setDraftFiles(response.discovery.files);
    setDraftLinks(response.discovery.links);
    setDraftOverview(response.discovery.overview);
    await onCorrectionApplied(response);
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
                  confidence: 0.5,
                  low_confidence_reasons: ["manual_field_added"],
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
        confidence: 1,
        low_confidence_reasons: [],
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
          <NarrativeSummary
            narrative={narrative}
            overview={draftOverview}
            editing={editing}
            warnings={warnings}
            files={draftFiles}
            numberSystem={numberSystem}
            onOverviewChange={setDraftOverview}
            onSelectFile={setActiveFileId}
          />

          <SchemaCorrectionPanel
            sessionId={sessionId}
            disabled={editing}
            draftFiles={draftFiles}
            fileById={fileById}
            fileNameMap={fileNameMap}
            numberSystem={numberSystem}
            onApplied={handleCorrectionApplied}
            onError={onCorrectionError}
          />

          <StatsStrip summary={summary} />

          <RelationshipDiagram
            files={draftFiles}
            links={draftLinks}
            fileNameMap={fileNameMap}
            linkStats={linkStats}
            onSelectFile={setActiveFileId}
          />

          <DocumentSummaryList
            files={draftFiles}
            doc={doc}
            fileById={fileById}
            fileNameMap={fileNameMap}
            warnings={warnings}
            onSelectFile={setActiveFileId}
            numberSystem={numberSystem}
          />

          <ConnectionSummaryList
            groups={relationshipGroups}
            fileNameMap={fileNameMap}
            linkStats={linkStats}
            onSelectFile={setActiveFileId}
            editing={editing}
            onAddLink={addLink}
            canAddLink={draftFiles.length >= 2}
            numberSystem={numberSystem}
          />

          <AgentContextSummary
            summary={guideSummary}
            files={draftFiles}
            onSelectFile={setActiveFileId}
            numberSystem={numberSystem}
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
        numberSystem={numberSystem}
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

      <div className="relative h-1.5 overflow-hidden bg-muted mb-5">
        <div
          className="h-full bg-primary transition-all duration-700 ease-out"
          style={{ width: `${progress}%` }}
        />
        {processing && !isDone && !latestError ? (
          <div className="absolute inset-y-0 left-0 w-1/3 animate-pulse bg-primary/30" />
        ) : null}
      </div>

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
                  <svg className="w-4 h-4 text-primary" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2.5}>
                    <path strokeLinecap="square" strokeLinejoin="miter" d="M5 13l4 4L19 7" />
                  </svg>
                ) : isActive ? (
                  <div className="w-3.5 h-3.5 border-2 border-primary border-t-transparent animate-spin" />
                ) : (
                  <div className="w-2 h-2 bg-muted-foreground/30" />
                )}
              </div>

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

function NarrativeSummary({
  narrative,
  overview,
  editing,
  warnings,
  files,
  numberSystem,
  onOverviewChange,
  onSelectFile,
}: {
  narrative: string;
  overview: string;
  editing: boolean;
  warnings: WarningState;
  files: DiscoveredFile[];
  numberSystem: NumberSystem;
  onOverviewChange: (value: string) => void;
  onSelectFile: (fileId: string) => void;
}) {
  const firstWarningFile = files.find((file) => warnings.byFile.has(file.file_id));
  return (
    <section className="grid gap-5 border-b border-border pb-6 xl:grid-cols-[minmax(0,1fr)_18rem]">
      <div className="min-w-0">
        <div className="small-caps text-xs text-primary">what cerno understood</div>
        {editing ? (
          <textarea
            value={overview}
            onChange={(event) => onOverviewChange(event.target.value)}
            rows={5}
            className="mt-3 w-full border border-border bg-card p-3 text-sm leading-6 focus:outline-none focus:ring-1 focus:ring-ring"
          />
        ) : (
          <p className="mt-3 max-w-4xl text-xl leading-8 text-foreground">
            {narrative}
          </p>
        )}
      </div>
      <div className="flex flex-col justify-between gap-3 border border-border bg-card p-4">
        <ConfidenceBadge
          low={warnings.total > 0}
          reasons={
            warnings.total > 0
              ? [
                  `${formatNumberValue(warnings.total, numberSystem)} item${
                    warnings.total === 1 ? "" : "s"
                  } need review`,
                ]
              : []
          }
        />
        {warnings.total > 0 && firstWarningFile ? (
          <button
            type="button"
            onClick={() => onSelectFile(firstWarningFile.file_id)}
            className="small-caps border border-border bg-muted px-3 py-2 text-xs text-foreground transition hover:border-primary"
          >
            review marked fields
          </button>
        ) : (
          <div className="text-sm leading-5 text-muted-foreground">
            Cerno found no low-confidence fields in this draft.
          </div>
        )}
      </div>
    </section>
  );
}

type CorrectionBusyState =
  | "interpreting"
  | "applying_minor"
  | "applying_structural"
  | null;

function SchemaCorrectionPanel({
  sessionId,
  disabled,
  draftFiles,
  fileById,
  fileNameMap,
  numberSystem,
  onApplied,
  onError,
}: {
  sessionId: string;
  disabled: boolean;
  draftFiles: DiscoveredFile[];
  fileById: Map<string, FileRecord>;
  fileNameMap: Map<string, string>;
  numberSystem: NumberSystem;
  onApplied: (response: SchemaCorrectionApplyResponse) => Promise<void> | void;
  onError: (message: string) => void;
}) {
  const [instruction, setInstruction] = useState("");
  const [patch, setPatch] = useState<SchemaCorrectionPatch | null>(null);
  const [selectedStructuralIds, setSelectedStructuralIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [appliedMinorIds, setAppliedMinorIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [appliedStructuralIds, setAppliedStructuralIds] = useState<Set<string>>(
    () => new Set(),
  );
  const [busy, setBusy] = useState<CorrectionBusyState>(null);
  const [error, setError] = useState<string | null>(null);

  const minorOps = useMemo(
    () => (patch?.operations ?? []).filter((op) => op.classification === "minor"),
    [patch],
  );
  const structuralOps = useMemo(
    () =>
      (patch?.operations ?? []).filter(
        (op) => op.classification === "structural",
      ),
    [patch],
  );
  const pendingStructuralOps = useMemo(
    () =>
      structuralOps.filter((op) => !appliedStructuralIds.has(op.op_id)),
    [structuralOps, appliedStructuralIds],
  );
  const selectedPendingIds = useMemo(
    () =>
      pendingStructuralOps
        .map((op) => op.op_id)
        .filter((opId) => selectedStructuralIds.has(opId)),
    [pendingStructuralOps, selectedStructuralIds],
  );
  const isBusy = busy !== null;

  const resetPatchState = () => {
    setPatch(null);
    setSelectedStructuralIds(new Set());
    setAppliedMinorIds(new Set());
    setAppliedStructuralIds(new Set());
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const cleanInstruction = instruction.trim();
    if (!cleanInstruction || disabled || isBusy) return;

    setError(null);
    resetPatchState();
    try {
      setBusy("interpreting");
      const interpreted = await interpretSchemaCorrection(sessionId, cleanInstruction);
      setPatch(interpreted);
      const nextMinorOps = interpreted.operations.filter(
        (op) => op.classification === "minor",
      );
      const nextStructuralOps = interpreted.operations.filter(
        (op) => op.classification === "structural",
      );
      setSelectedStructuralIds(new Set(nextStructuralOps.map((op) => op.op_id)));

      if (interpreted.operations.length === 0) {
        setError("Cerno could not find a schema change in that correction.");
        return;
      }

      if (nextMinorOps.length > 0) {
        setBusy("applying_minor");
        const result = await applySchemaCorrection(
          sessionId,
          { ...interpreted, operations: nextMinorOps },
          [],
        );
        setAppliedMinorIds(new Set(nextMinorOps.map((op) => op.op_id)));
        await onApplied(result);
      }
    } catch (err) {
      const message = (err as Error).message;
      setError(message);
      onError(message);
    } finally {
      setBusy(null);
    }
  };

  const approveStructuralOps = async (opIds: string[]) => {
    if (!patch || opIds.length === 0 || isBusy) return;
    const idSet = new Set(opIds);
    const operations = structuralOps.filter((op) => idSet.has(op.op_id));
    if (operations.length === 0) return;

    setError(null);
    try {
      setBusy("applying_structural");
      const result = await applySchemaCorrection(
        sessionId,
        { ...patch, operations },
        operations.map((op) => op.op_id),
      );
      setAppliedStructuralIds((current) => {
        const next = new Set(current);
        for (const op of operations) next.add(op.op_id);
        return next;
      });
      setSelectedStructuralIds((current) => {
        const next = new Set(current);
        for (const op of operations) next.delete(op.op_id);
        return next;
      });
      await onApplied(result);
    } catch (err) {
      const message = (err as Error).message;
      setError(message);
      onError(message);
    } finally {
      setBusy(null);
    }
  };

  const toggleStructuralOp = (opId: string) => {
    setSelectedStructuralIds((current) => {
      const next = new Set(current);
      if (next.has(opId)) next.delete(opId);
      else next.add(opId);
      return next;
    });
  };

  const selectAllStructuralOps = () => {
    setSelectedStructuralIds(new Set(pendingStructuralOps.map((op) => op.op_id)));
  };

  return (
    <section className="border border-border bg-card p-4">
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_18rem]">
        <form onSubmit={handleSubmit} className="min-w-0">
          <div className="small-caps text-xs text-primary">schema correction</div>
          <label className="mt-2 block font-mono text-lg text-foreground">
            Tell Cerno about a correction
          </label>
          <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">
            This updates what Cerno understood about the files. It is separate from chat.
          </p>
          <div className="mt-4 flex flex-col gap-3 lg:flex-row">
            <textarea
              value={instruction}
              onChange={(event) => setInstruction(event.target.value)}
              disabled={disabled || isBusy}
              rows={3}
              placeholder="Example: the amount column is in lakhs, or file A's date is DD/MM."
              className="min-h-[5.5rem] flex-1 border border-border bg-background p-3 text-sm leading-6 text-foreground placeholder:text-muted-foreground/60 focus:outline-none focus:ring-1 focus:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
            />
            <button
              type="submit"
              disabled={!instruction.trim() || disabled || isBusy}
              className="small-caps border border-foreground bg-primary px-4 py-2 text-xs text-primary-foreground transition hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-40 lg:self-start"
            >
              {busy === "interpreting" ? "reading" : "preview correction"}
            </button>
          </div>
        </form>
        <div className="border border-border bg-muted/40 p-3 text-sm leading-6 text-muted-foreground">
          {disabled ? (
            "Finish or cancel structured editing before applying a natural-language correction."
          ) : busy === "applying_structural" ? (
            "Re-casting data and regenerating the data guide..."
          ) : busy === "applying_minor" ? (
            "Applying metadata-only changes..."
          ) : (
            "Minor wording changes apply automatically. Structural changes always need approval."
          )}
        </div>
      </div>

      {isBusy ? (
        <div className="mt-4 h-1.5 overflow-hidden bg-muted">
          <div className="h-full w-1/2 animate-pulse bg-primary" />
        </div>
      ) : null}

      {error ? (
        <div className="mt-4 border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      ) : null}

      {patch ? (
        <CorrectionPatchPreview
          minorOps={minorOps}
          structuralOps={structuralOps}
          pendingStructuralOps={pendingStructuralOps}
          selectedStructuralIds={selectedStructuralIds}
          selectedPendingIds={selectedPendingIds}
          appliedMinorIds={appliedMinorIds}
          appliedStructuralIds={appliedStructuralIds}
          draftFiles={draftFiles}
          fileById={fileById}
          fileNameMap={fileNameMap}
          numberSystem={numberSystem}
          busy={busy}
          onToggleStructuralOp={toggleStructuralOp}
          onSelectAllStructuralOps={selectAllStructuralOps}
          onApproveSelected={() => approveStructuralOps(selectedPendingIds)}
          onApproveAll={() =>
            approveStructuralOps(pendingStructuralOps.map((op) => op.op_id))
          }
        />
      ) : null}
    </section>
  );
}

function CorrectionPatchPreview({
  minorOps,
  structuralOps,
  pendingStructuralOps,
  selectedStructuralIds,
  selectedPendingIds,
  appliedMinorIds,
  appliedStructuralIds,
  draftFiles,
  fileById,
  fileNameMap,
  numberSystem,
  busy,
  onToggleStructuralOp,
  onSelectAllStructuralOps,
  onApproveSelected,
  onApproveAll,
}: {
  minorOps: SchemaCorrectionOperation[];
  structuralOps: SchemaCorrectionOperation[];
  pendingStructuralOps: SchemaCorrectionOperation[];
  selectedStructuralIds: Set<string>;
  selectedPendingIds: string[];
  appliedMinorIds: Set<string>;
  appliedStructuralIds: Set<string>;
  draftFiles: DiscoveredFile[];
  fileById: Map<string, FileRecord>;
  fileNameMap: Map<string, string>;
  numberSystem: NumberSystem;
  busy: CorrectionBusyState;
  onToggleStructuralOp: (opId: string) => void;
  onSelectAllStructuralOps: () => void;
  onApproveSelected: () => void;
  onApproveAll: () => void;
}) {
  const hasPendingStructural = pendingStructuralOps.length > 0;
  return (
    <div className="mt-5 grid gap-4">
      {minorOps.length > 0 ? (
        <CorrectionGroup
          title="Applied metadata changes"
          description="These do not re-cast stored data."
          count={minorOps.length}
          numberSystem={numberSystem}
        >
          {minorOps.map((operation) => (
            <CorrectionOperationCard
              key={operation.op_id}
              operation={operation}
              applied={appliedMinorIds.has(operation.op_id)}
              draftFiles={draftFiles}
              fileById={fileById}
              fileNameMap={fileNameMap}
              numberSystem={numberSystem}
            />
          ))}
        </CorrectionGroup>
      ) : null}

      {structuralOps.length > 0 ? (
        <CorrectionGroup
          title="Structural changes need approval"
          description="Approving these rewrites stored data or relationships and regenerates the data guide."
          count={structuralOps.length}
          numberSystem={numberSystem}
          action={
            hasPendingStructural ? (
              <div className="flex flex-wrap gap-2">
                <button
                  type="button"
                  onClick={onSelectAllStructuralOps}
                  disabled={busy !== null}
                  className="small-caps border border-border bg-muted px-3 py-2 text-[10px] text-foreground transition hover:border-primary disabled:opacity-40"
                >
                  select all
                </button>
                <button
                  type="button"
                  onClick={onApproveSelected}
                  disabled={selectedPendingIds.length === 0 || busy !== null}
                  className="small-caps border border-foreground bg-primary px-3 py-2 text-[10px] text-primary-foreground transition hover:bg-primary/90 disabled:opacity-40"
                >
                  approve selected
                </button>
                <button
                  type="button"
                  onClick={onApproveAll}
                  disabled={busy !== null}
                  className="small-caps border border-foreground/28 bg-muted/70 px-3 py-2 text-[10px] text-foreground transition hover:bg-card disabled:opacity-40"
                >
                  approve all
                </button>
              </div>
            ) : null
          }
        >
          {structuralOps.map((operation) => (
            <CorrectionOperationCard
              key={operation.op_id}
              operation={operation}
              applied={appliedStructuralIds.has(operation.op_id)}
              selected={selectedStructuralIds.has(operation.op_id)}
              structural
              draftFiles={draftFiles}
              fileById={fileById}
              fileNameMap={fileNameMap}
              numberSystem={numberSystem}
              onToggle={() => onToggleStructuralOp(operation.op_id)}
            />
          ))}
        </CorrectionGroup>
      ) : null}
    </div>
  );
}

function CorrectionGroup({
  title,
  description,
  count,
  numberSystem,
  action,
  children,
}: {
  title: string;
  description: string;
  count: number;
  numberSystem: NumberSystem;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="border border-border bg-background">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-border p-3">
        <div>
          <div className="small-caps text-[10px] text-muted-foreground">
            {formatNumberValue(count, numberSystem)} change{count === 1 ? "" : "s"}
          </div>
          <h4 className="mt-1 font-mono text-base text-foreground">{title}</h4>
          <p className="mt-1 text-sm leading-5 text-muted-foreground">{description}</p>
        </div>
        {action}
      </div>
      <div className="divide-y divide-border">{children}</div>
    </div>
  );
}

function CorrectionOperationCard({
  operation,
  applied,
  selected,
  structural = false,
  draftFiles,
  fileById,
  fileNameMap,
  numberSystem,
  onToggle,
}: {
  operation: SchemaCorrectionOperation;
  applied: boolean;
  selected?: boolean;
  structural?: boolean;
  draftFiles: DiscoveredFile[];
  fileById: Map<string, FileRecord>;
  fileNameMap: Map<string, string>;
  numberSystem: NumberSystem;
  onToggle?: () => void;
}) {
  const target = correctionTargetLabel(operation, draftFiles, fileNameMap);
  const impact = structural
    ? structuralCorrectionImpact(operation, fileById, numberSystem)
    : "Applied to the data map only; stored values were not rewritten.";
  const before = formatCorrectionValue(operation.before_value, operation, numberSystem, "before");
  const after = formatCorrectionValue(operation.after_value, operation, numberSystem, "after");

  return (
    <div className="grid gap-3 p-3 md:grid-cols-[minmax(0,1fr)_minmax(16rem,0.7fr)]">
      <div className="min-w-0">
        <div className="flex items-start gap-3">
          {structural && !applied ? (
            <input
              type="checkbox"
              checked={Boolean(selected)}
              onChange={onToggle}
              className="mt-1 h-4 w-4 accent-primary"
              aria-label={`Select ${operation.description}`}
            />
          ) : null}
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-sm text-foreground">
                {operationTitle(operation)}
              </span>
              <span
                className={`small-caps border px-2 py-0.5 text-[10px] ${
                  applied
                    ? "border-primary/30 bg-primary/10 text-primary"
                    : structural
                      ? "border-destructive/40 bg-destructive/10 text-destructive"
                      : "border-border bg-muted text-muted-foreground"
                }`}
              >
                {applied ? "applied" : structural ? "needs approval" : "pending"}
              </span>
            </div>
            <div className="mt-1 text-sm text-muted-foreground">{target}</div>
            <p className="mt-2 text-sm leading-5 text-foreground/80">
              {operation.description}
            </p>
            <p className="mt-2 text-xs leading-5 text-muted-foreground">{impact}</p>
          </div>
        </div>
      </div>
      <div className="grid gap-2 text-xs">
        <DiffValue label="Before" value={before} />
        <DiffValue label="After" value={after} strong />
      </div>
    </div>
  );
}

function DiffValue({
  label,
  value,
  strong = false,
}: {
  label: string;
  value: string;
  strong?: boolean;
}) {
  return (
    <div className="grid gap-1 border border-border bg-card px-3 py-2">
      <div className="small-caps text-[10px] text-muted-foreground">{label}</div>
      <div className={`break-words font-mono ${strong ? "text-foreground" : "text-muted-foreground"}`}>
        {value}
      </div>
    </div>
  );
}

function StatsStrip({ summary }: { summary: SummaryStats }) {
  return (
    <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-6">
      <Stat label="files" value={summary.files} />
      <Stat label="rows" value={summary.rows} />
      <Stat label="fields" value={summary.columns} />
      <Stat label="connections" value={summary.relationships} />
      <Stat label="size" value={summary.size} />
      <Stat label="state" value={summary.state} />
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="border border-border bg-card px-3 py-3">
      <div className="small-caps text-[11px] text-muted-foreground">{label}</div>
      <div className="mt-1 font-mono text-lg text-foreground">{value}</div>
    </div>
  );
}

function ConfidenceBadge({
  low,
  reasons,
  compact = false,
}: {
  low: boolean;
  reasons: string[];
  compact?: boolean;
}) {
  const friendlyReasons = reasons.map(friendlyReason).filter(Boolean);
  const label = low ? "needs review" : "confirmed";
  const title = friendlyReasons.join("\n") || label;
  return (
    <span
      title={title}
      className={`small-caps inline-flex w-fit items-center border px-2 py-1 text-[10px] ${
        low
          ? "border-destructive/40 bg-destructive/10 text-destructive"
          : "border-primary/30 bg-primary/10 text-primary"
      }`}
    >
      {compact && low ? "review" : label}
    </span>
  );
}

function RelationshipDiagram({
  files,
  links,
  fileNameMap,
  linkStats,
  onSelectFile,
}: {
  files: DiscoveredFile[];
  links: DiscoveredLink[];
  fileNameMap: Map<string, string>;
  linkStats: Map<string, LinkStat>;
  onSelectFile: (fileId: string) => void;
}) {
  const { theme } = useTheme();
  const option = useMemo(
    () => buildRelationshipGraphOption(files, links, fileNameMap, linkStats),
    [files, links, fileNameMap, linkStats, theme],
  );
  const handleGraphClick = (params: Record<string, unknown>) => {
    if (params.dataType !== "node") return;
    const data = params.data;
    if (!data || typeof data !== "object") return;
    const id = (data as { id?: unknown }).id;
    if (typeof id === "string") onSelectFile(id);
  };

  return (
    <section className="border-b border-border pb-7">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
        <div>
          <div className="small-caps text-xs text-primary">connections</div>
          <h3 className="mt-1 font-mono text-xl text-foreground">How the files link together</h3>
        </div>
        <div className="text-xs text-muted-foreground">Click a document node to inspect it.</div>
      </div>
      <div className="border border-border bg-card p-3">
        {files.length === 0 ? (
          <div className="p-4 text-sm text-muted-foreground">No files available.</div>
        ) : links.length === 0 ? (
          <div className="grid min-h-[18rem] place-items-center text-center text-sm text-muted-foreground">
            <div>
              <div className="font-mono text-base text-foreground">No strong file links yet</div>
              <p className="mt-2 max-w-md leading-6">
                Cerno still understands each file on its own. Add or approve links from the detail drawer when needed.
              </p>
            </div>
          </div>
        ) : (
          <EChart option={option} height={360} onClick={handleGraphClick} />
        )}
      </div>
    </section>
  );
}

function DocumentSummaryList({
  files,
  doc,
  fileById,
  fileNameMap,
  warnings,
  onSelectFile,
  numberSystem,
}: {
  files: DiscoveredFile[];
  doc: DataDoc | null;
  fileById: Map<string, FileRecord>;
  fileNameMap: Map<string, string>;
  warnings: WarningState;
  onSelectFile: (fileId: string) => void;
  numberSystem: NumberSystem;
}) {
  const docFiles = useMemo(
    () => new Map((doc?.files ?? []).map((file) => [file.file_id, file])),
    [doc],
  );
  return (
    <section className="border-b border-foreground/12 pb-7">
      <div className="mb-4 flex items-end justify-between gap-4">
        <div>
          <div className="small-caps text-xs text-primary">documents</div>
          <h3 className="mt-1 font-mono text-xl text-foreground">Files Cerno understood</h3>
        </div>
        <div className="text-xs text-muted-foreground">Click a card for fields and sample rows.</div>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {files.map((file) => {
          const record = fileById.get(file.file_id);
          const fileWarnings = warnings.byFile.get(file.file_id) ?? [];
          const docFile = docFiles.get(file.file_id);
          const keyFields = pickKeyFields(file, docFile);
          return (
            <button
              key={file.file_id}
              type="button"
              onClick={() => onSelectFile(file.file_id)}
              className="group flex min-h-[15rem] w-full flex-col justify-between border border-border bg-card p-4 text-left transition hover:border-primary"
            >
              <div className="min-w-0">
                <div className="flex items-start justify-between gap-3">
                  <span className="truncate font-mono text-lg text-foreground" title={record?.filename ?? undefined}>
                    {fileNameMap.get(file.file_id) ?? file.friendly_name}
                  </span>
                  <ConfidenceBadge
                    low={fileWarnings.length > 0}
                    reasons={fileWarnings}
                    compact
                  />
                </div>
                <p className="mt-3 text-sm leading-6 text-muted-foreground">
                  {docFile?.grain || inferGrain(file) || "One row in this file."}
                </p>
                <p className="mt-2 text-sm leading-6 text-foreground/72">
                  {shortText(file.description || docFile?.description || "No description yet.", 140)}
                </p>
                {fileWarnings.length > 0 ? (
                  <div className="mt-3 grid gap-1 text-xs text-destructive">
                    {fileWarnings.slice(0, 2).map((warning) => (
                      <div key={warning}>{friendlyReason(warning)}</div>
                    ))}
                  </div>
                ) : null}
              </div>
              <div className="mt-5">
                <div className="mb-2 small-caps text-[11px] text-muted-foreground">
                  key fields
                </div>
                <div className="flex flex-wrap gap-2">
                  {keyFields.map((field) => (
                    <span
                      key={`${file.file_id}:${field.name}`}
                      className="border border-border bg-muted px-2 py-1 text-xs text-foreground"
                    >
                      {friendlyFieldLabel(field.name, field.dtype)} · {field.name}
                    </span>
                  ))}
                </div>
              </div>
              <div className="mt-5 flex items-center justify-between border-t border-border pt-3 font-mono text-xs text-muted-foreground">
                <span>{formatNumberValue(record?.row_count ?? 0, numberSystem)} rows</span>
                <span>{formatNumberValue(file.columns.length, numberSystem)} fields</span>
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
  numberSystem,
}: {
  groups: RelationshipGroup[];
  fileNameMap: Map<string, string>;
  linkStats: Map<string, LinkStat>;
  onSelectFile: (fileId: string) => void;
  editing: boolean;
  onAddLink: () => void;
  canAddLink: boolean;
  numberSystem: NumberSystem;
}) {
  const visibleGroups = [...groups]
    .sort((a, b) => b.links.length - a.links.length)
    .slice(0, 6);
  const hiddenCount = Math.max(0, groups.length - visibleGroups.length);

  return (
    <section className="border-b border-foreground/12 pb-7">
      <details className="group border border-border bg-card">
        <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-4 py-3">
          <div>
            <div className="small-caps text-xs text-primary">connection details</div>
            <h3 className="mt-1 font-mono text-base text-foreground">View relationship list</h3>
          </div>
          <span className="small-caps text-[10px] text-muted-foreground group-open:hidden">
            expand
          </span>
          <span className="small-caps hidden text-[10px] text-muted-foreground group-open:inline">
            collapse
          </span>
        </summary>
        <div className="border-t border-border p-4">
          <div className="mb-4 flex flex-wrap items-end justify-between gap-4">
            <div className="text-sm text-muted-foreground">
              These are the same links shown in the diagram, with editable fields.
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
                  {formatNumberValue(group.links.length, numberSystem)} link{group.links.length === 1 ? "" : "s"}
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
                  <div className="text-xs text-destructive md:col-span-4">
                    {formatNumberValue(weakCount, numberSystem)} weak link{weakCount === 1 ? "" : "s"}.
                  </div>
                ) : null}
              </div>
            );
          })}
          {hiddenCount > 0 ? (
            <div className="small-caps px-3 py-2 text-xs text-foreground/42">
              {formatNumberValue(hiddenCount, numberSystem)} more connection group{hiddenCount === 1 ? "" : "s"} hidden from this view.
            </div>
          ) : null}
        </div>
      )}
        </div>
      </details>
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
  numberSystem,
}: {
  summary: GuideSummary;
  files: DiscoveredFile[];
  onSelectFile: (fileId: string) => void;
  numberSystem: NumberSystem;
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
        <ContextCount label="documents" value={summary.documented} numberSystem={numberSystem} />
        <ContextCount label="notes" value={summary.notes} numberSystem={numberSystem} />
        <ContextCount label="glossary" value={summary.glossary} numberSystem={numberSystem} />
        <ContextCount label="questions" value={summary.questions} numberSystem={numberSystem} />
        <ContextCount label="watch-outs" value={summary.caveats} numberSystem={numberSystem} />
      </div>
    </section>
  );
}

function ContextCount({
  label,
  value,
  numberSystem,
}: {
  label: string;
  value: number;
  numberSystem: NumberSystem;
}) {
  return (
    <div className="border border-foreground/10 bg-card/52 px-3 py-3">
      <div className="font-mono text-xl text-foreground">
        {formatNumberValue(value, numberSystem)}
      </div>
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
  numberSystem,
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
  numberSystem: NumberSystem;
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
                title={record?.filename ?? undefined}
                onChange={(e) => onUpdateFile(fileIdx, { friendly_name: e.target.value })}
                className="mt-2 w-full border border-border bg-card px-2 py-1 font-mono text-xl focus:outline-none focus:ring-1 focus:ring-ring"
              />
            ) : (
              <h3
                className="mt-1 truncate font-mono text-xl text-foreground"
                title={record?.filename ?? undefined}
              >
                {file.friendly_name}
              </h3>
            )}
            <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
              <span>{formatSize(record?.original_size_bytes, numberSystem)}</span>
              <span>{formatNumberValue(record?.row_count ?? 0, numberSystem)} rows</span>
              <span>{formatNumberValue(file.columns.length, numberSystem)} fields</span>
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
            <div className="border border-destructive/40 bg-destructive/10 p-3 text-xs text-destructive">
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
            <HeaderPreview
              preview={preview}
              headerRow={file.header_row}
              editing={editing}
              onHeaderRowChange={(headerRow) => onUpdateFile(fileIdx, { header_row: headerRow })}
              numberSystem={numberSystem}
            />
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
            <details open={editing} className="group border border-border bg-card">
              <summary className="flex cursor-pointer list-none items-center justify-between px-3 py-3 text-sm text-foreground">
                <span className="font-mono">View fields</span>
                <span className="small-caps text-[10px] text-muted-foreground group-open:hidden">
                  {formatNumberValue(file.columns.length, numberSystem)} hidden
                </span>
                <span className="small-caps hidden text-[10px] text-muted-foreground group-open:inline">
                  hide fields
                </span>
              </summary>
              <SchemaColumnTable
                file={file}
                fileIdx={fileIdx}
                editing={editing}
                onUpdateColumn={onUpdateColumn}
                onRemoveColumn={onRemoveColumn}
              />
            </details>
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
                  {formatNumberValue(preview.total_rows, numberSystem)} rows
                </div>
              ) : null}
            </div>
            {previewLoading ? (
              <div className="py-4 text-sm text-muted-foreground">Loading preview...</div>
            ) : previewError ? (
              <div className="py-4 text-sm text-destructive">{previewError}</div>
            ) : preview ? (
              <PreviewTable preview={preview} numberSystem={numberSystem} />
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
                  col.description || <span className="text-destructive">Needs description</span>
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
                  {link.summary || relationshipSentence(link, fileNameMap)}
                </div>
                <div className="mt-2 flex flex-wrap gap-2 text-xs text-muted-foreground">
                  <span className="small-caps border border-border px-1.5 py-0.5 text-[10px]">
                    {link.direction.replace(/_/g, " ")}
                  </span>
                  <EvidenceLabel stat={stat} link={link} />
                </div>
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

function HeaderPreview({
  preview,
  headerRow,
  editing,
  onHeaderRowChange,
  numberSystem,
}: {
  preview: FilePreviewResponse | null;
  headerRow: number;
  editing: boolean;
  onHeaderRowChange: (headerRow: number) => void;
  numberSystem: NumberSystem;
}) {
  const rows = preview?.rows.slice(0, 5) ?? [];
  const safeHeaderRow = Math.max(0, headerRow);

  return (
    <div className="mt-4 border border-border bg-card p-3">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="small-caps text-[10px] text-muted-foreground">detected header</div>
          <div className="mt-1 text-sm text-foreground">
            Row {formatNumberValue(safeHeaderRow + 1, numberSystem)} is treated as the field names.
          </div>
        </div>
        {editing ? (
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            Header row
            <input
              type="number"
              min={0}
              value={safeHeaderRow}
              onChange={(event) =>
                onHeaderRowChange(Math.max(0, Number(event.target.value) || 0))
              }
              className="w-20 border border-border bg-card px-2 py-1 text-foreground"
            />
          </label>
        ) : null}
      </div>
      {rows.length === 0 ? (
        <div className="text-xs text-muted-foreground">Preview unavailable.</div>
      ) : (
        <div className="grid gap-1 overflow-x-auto">
          {rows.map((row, index) => {
            const isHeader = index === Math.min(safeHeaderRow, rows.length - 1);
            return (
              <div
                key={index}
                className={`grid min-w-[34rem] grid-cols-[4rem_1fr] border px-2 py-2 text-xs ${
                  isHeader
                    ? "border-primary bg-primary/10 text-foreground"
                    : "border-border bg-muted/40 text-muted-foreground"
                }`}
              >
                <span className="small-caps text-[10px]">
                  row {formatNumberValue(index + 1, numberSystem)}
                </span>
                <span className="truncate font-mono">
                  {row
                    .map((value) => formatCell(value, numberSystem))
                    .filter(Boolean)
                    .slice(0, 6)
                    .join(" | ") || "empty"}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function PreviewTable({
  preview,
  numberSystem,
}: {
  preview: FilePreviewResponse;
  numberSystem: NumberSystem;
}) {
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
                  title={formatCell(row[colIdx], numberSystem)}
                >
                  {formatCell(row[colIdx], numberSystem) || <span className="text-muted-foreground/50">empty</span>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="small-caps px-4 py-3 text-[10px] text-muted-foreground">
        Showing {formatNumberValue(preview.rows.length, numberSystem)} of {formatNumberValue(preview.total_rows, numberSystem)} rows
      </div>
    </div>
  );
}

function EvidenceLabel({
  stat,
  link,
}: {
  stat?: LinkStat;
  link: DiscoveredLink;
}) {
  const low = needsReview(link.confidence, link.low_confidence_reasons);
  const source = stat?.source === "user_added" ? "manual" : "value scan";
  return (
    <span className={low ? "text-destructive" : "text-muted-foreground"}>
      {low ? "needs review" : "confirmed"} by {source}
    </span>
  );
}

function buildNarrative(
  overview: string,
  files: DiscoveredFile[],
  links: DiscoveredLink[],
  fileNameMap: Map<string, string>,
  numberSystem: NumberSystem,
): string {
  if (files.length === 0) {
    return "Cerno has not built a data map yet.";
  }

  const fileNames = files.map(
    (file) => fileNameMap.get(file.file_id) ?? file.friendly_name,
  );
  const totalFields = files.reduce((sum, file) => sum + file.columns.length, 0);
  const base = `Cerno read ${formatNumberValue(files.length, numberSystem)} file${
    files.length === 1 ? "" : "s"
  }: ${joinReadable(fileNames, numberSystem)}. It found ${formatNumberValue(
    totalFields,
    numberSystem,
  )} field${totalFields === 1 ? "" : "s"} and ${formatNumberValue(
    links.length,
    numberSystem,
  )} connection${links.length === 1 ? "" : "s"} between them.`;
  const cleanOverview = overview.replace(/\s+/g, " ").trim();
  if (!cleanOverview) return base;
  return `${base} ${shortText(cleanOverview, 220)}`;
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
  numberSystem: NumberSystem,
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
        : `${formatSize(totalSize, numberSystem)}${hasUnknownSize ? " +" : ""}`,
    rows: formatNumberValue(totalRows, numberSystem),
    files: formatNumberValue(draftFiles.length || files.length, numberSystem),
    columns: formatNumberValue(columnCount, numberSystem),
    relationships: formatNumberValue(draftLinks.length, numberSystem),
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
    for (const column of file.columns) {
      const reasons = reviewReasons(
        column.confidence,
        column.low_confidence_reasons,
      );
      if (reasons.length > 0) {
        add(
          file.file_id,
          `${column.name} needs review: ${friendlyReason(reasons[0]!)}`
        );
      }
    }
    if (file.header_row > 5) {
      add(file.file_id, `Header row ${file.header_row} is unusually deep.`);
    }
  }

  const linkedFiles = new Set<string>();
  for (const link of links) {
    linkedFiles.add(link.file_a_id);
    linkedFiles.add(link.file_b_id);
    const reasons = reviewReasons(link.confidence, link.low_confidence_reasons);
    if (reasons.length > 0) {
      const message = `${link.col_a} to ${link.col_b} connection needs review: ${friendlyReason(
        reasons[0]!,
      )}`;
      add(link.file_a_id, message);
      add(link.file_b_id, message);
    }
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

type GraphFormatterParam = {
  dataType?: string;
  name?: string;
  data?: {
    name?: string;
    value?: string;
  };
};

// ECharts' canvas renderer cannot resolve CSS custom properties like
// `hsl(var(--primary))`, so it falls back to black. Read the computed HSL
// triplet from the document and wrap it into a concrete color string.
function cssHsl(name: string, fallback: string): string {
  if (typeof document === "undefined") return fallback;
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value ? `hsl(${value})` : fallback;
}

function buildRelationshipGraphOption(
  files: DiscoveredFile[],
  links: DiscoveredLink[],
  fileNameMap: Map<string, string>,
  linkStats: Map<string, LinkStat>,
): Record<string, unknown> {
  const primary = cssHsl("--primary", "#6E66C9");
  const destructive = cssHsl("--destructive", "#e0492f");
  const border = cssHsl("--border", "#cfc9e6");
  const foreground = cssHsl("--foreground", "#15131c");
  const mutedForeground = cssHsl("--muted-foreground", "#6b6680");
  const card = cssHsl("--card", "#ffffff");

  const fileIds = new Set(files.map((file) => file.file_id));
  const nodes = files.map((file) => {
    const low = file.columns.some((column) =>
      needsReview(column.confidence, column.low_confidence_reasons),
    );
    return {
      id: file.file_id,
      name: fileNameMap.get(file.file_id) ?? file.friendly_name,
      symbolSize: Math.max(26, Math.min(46, 22 + Math.log2(file.columns.length + 1) * 6)),
      itemStyle: {
        color: low ? destructive : primary,
        borderColor: border,
        borderWidth: 1,
      },
    };
  });
  const graphLinks = links
    .filter((link) => fileIds.has(link.file_a_id) && fileIds.has(link.file_b_id))
    .map((link) => {
      const stat = linkStats.get(
        linkLookupKey(link.file_a_id, link.col_a, link.file_b_id, link.col_b),
      );
      const low = needsReview(link.confidence, link.low_confidence_reasons);
      const weak = low || isWeakLink(stat);
      return {
        source: link.file_a_id,
        target: link.file_b_id,
        value: relationshipLabel(link, fileNameMap),
        lineStyle: {
          color: weak ? destructive : primary,
          opacity: weak ? 0.4 : 0.65,
          width: weak ? 1 : 2,
          curveness: 0.08,
        },
      };
    });

  return {
    animation: false,
    tooltip: {
      confine: true,
      formatter: (params: GraphFormatterParam) => {
        if (params.dataType === "edge") return params.data?.value ?? "";
        return params.name ?? params.data?.name ?? "";
      },
    },
    series: [
      {
        type: "graph",
        // Circular layout is deterministic and always centered — no force
        // simulation, so the diagram renders once and never jitters.
        layout: "circular",
        circular: { rotateLabel: false },
        roam: true,
        draggable: true,
        data: nodes,
        links: graphLinks,
        label: {
          show: true,
          position: "bottom",
          distance: 4,
          color: foreground,
          fontSize: 11,
          fontFamily: "'JetBrains Mono', ui-monospace, monospace",
          formatter: (params: GraphFormatterParam) => shortText(params.name ?? "", 22),
        },
        // Edge labels are hidden by default — with many cross-links they
        // overlap into an unreadable mess. They appear on hover instead.
        edgeLabel: { show: false },
        emphasis: {
          focus: "adjacency",
          edgeLabel: {
            show: true,
            color: mutedForeground,
            backgroundColor: card,
            padding: [2, 4],
            fontSize: 10,
            formatter: (params: GraphFormatterParam) => shortText(params.data?.value ?? "", 48),
          },
        },
      },
    ],
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
  const direction = link.direction.replace(/_/g, " ");
  return `${relationshipLabel(link, fileNameMap)}. This looks like ${direction}.`;
}

function relationshipLabel(
  link: DiscoveredLink,
  fileNameMap: Map<string, string>,
): string {
  const left = fileNameMap.get(link.file_a_id) ?? link.file_a_id;
  const right = fileNameMap.get(link.file_b_id) ?? link.file_b_id;
  const column =
    link.col_a.trim().toLowerCase() === link.col_b.trim().toLowerCase()
      ? humanizeLabel(link.col_a)
      : `${humanizeLabel(link.col_a)} / ${humanizeLabel(link.col_b)}`;
  return `${column} connects ${left} -> ${right}`;
}

function operationTitle(operation: SchemaCorrectionOperation): string {
  const labels: Record<string, string> = {
    add_column: "Add field",
    add_link: "Add connection",
    edit_link: "Edit connection",
    remove_column: "Remove field",
    remove_link: "Remove connection",
    rename_column: "Rename field",
    scale_column: "Rescale values",
    set_column_description: "Update field meaning",
    set_dtype: "Change field type",
    set_file_description: "Update file description",
    set_friendly_name: "Rename file",
    set_glossary_term: "Update glossary",
    set_header_row: "Change header row",
    set_starter_question: "Update starter question",
    set_usage_note: "Update usage note",
  };
  return labels[operation.op_type] ?? humanizeLabel(operation.op_type);
}

function correctionTargetLabel(
  operation: SchemaCorrectionOperation,
  files: DiscoveredFile[],
  fileNameMap: Map<string, string>,
): string {
  if (operation.target_type === "data_doc") return "Data guide";
  if (operation.target_type === "link") {
    const leftId = operation.target.file_a_id;
    const rightId = operation.target.file_b_id;
    const left = leftId ? fileNameMap.get(leftId) ?? leftId : "source file";
    const right = rightId ? fileNameMap.get(rightId) ?? rightId : "target file";
    const leftColumn = operation.target.col_a ?? operation.target.left_column;
    const rightColumn = operation.target.col_b ?? operation.target.right_column;
    if (leftColumn || rightColumn) {
      return `${left} ${leftColumn ?? ""} -> ${right} ${rightColumn ?? ""}`.trim();
    }
    return `${left} -> ${right}`;
  }

  const file = findCorrectionFile(operation, files);
  const fileLabel = file
    ? fileNameMap.get(file.file_id) ?? file.friendly_name
    : operation.target.file_id ?? "File";
  if (operation.target_type === "file") return fileLabel;

  const column = findCorrectionColumn(operation, files);
  return `${fileLabel} · ${column?.name ?? operation.target.column_id ?? "Field"}`;
}

function findCorrectionFile(
  operation: SchemaCorrectionOperation,
  files: DiscoveredFile[],
): DiscoveredFile | undefined {
  const fileId = extractTargetFileId(operation);
  if (!fileId) return undefined;
  return files.find((file) => file.file_id === fileId);
}

function findCorrectionColumn(
  operation: SchemaCorrectionOperation,
  files: DiscoveredFile[],
): DiscoveredColumn | undefined {
  const file = findCorrectionFile(operation, files);
  if (!file) return undefined;
  const columnId = operation.target.column_id ?? operation.target.column;
  if (!columnId) return undefined;
  return file.columns.find(
    (column) => column.column_id === columnId || column.name === columnId,
  );
}

function structuralCorrectionImpact(
  operation: SchemaCorrectionOperation,
  fileById: Map<string, FileRecord>,
  numberSystem: NumberSystem,
): string {
  if (operation.target_type === "link") {
    return "Will update relationships and regenerate the data guide.";
  }
  const fileId = extractTargetFileId(operation);
  const rowCount = fileId ? fileById.get(fileId)?.row_count : undefined;
  const rows =
    typeof rowCount === "number"
      ? `${formatNumberValue(rowCount, numberSystem)} row${
          rowCount === 1 ? "" : "s"
        }`
      : "the affected rows";
  if (operation.op_type === "set_header_row") {
    return `Will re-read the header, re-cast ${rows}, and regenerate the data guide.`;
  }
  if (operation.target_type === "column") {
    return `Will re-cast up to ${rows} for this field and regenerate the data guide.`;
  }
  return "Will rewrite the stored data map and regenerate the data guide.";
}

function extractTargetFileId(operation: SchemaCorrectionOperation): string | undefined {
  return (
    operation.target.file_id ??
    operation.target.file_a_id ??
    operation.target.left_file_id
  );
}

function formatCorrectionValue(
  value: unknown,
  operation: SchemaCorrectionOperation,
  numberSystem: NumberSystem,
  side: "before" | "after",
): string {
  if (operation.op_type === "scale_column" && side === "after") {
    const factor = operation.transform?.factor;
    if (typeof factor === "number") {
      return `multiply values by ${formatNumberValue(factor, numberSystem)}`;
    }
  }
  if (value === null || value === undefined || value === "") return "empty";
  if (typeof value === "number") return formatNumberValue(value, numberSystem);
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "string") return shortText(value, 180);
  if (Array.isArray(value)) {
    return value
      .map((item) => formatCorrectionValue(item, operation, numberSystem, side))
      .join(", ");
  }
  if (typeof value === "object") {
    return Object.entries(value)
      .map(([key, item]) => {
        const formatted =
          typeof item === "number"
            ? formatNumberValue(item, numberSystem)
            : typeof item === "string"
              ? item
              : JSON.stringify(item);
        return `${humanizeLabel(key)}: ${formatted}`;
      })
      .join(", ");
  }
  return String(value);
}

function pickKeyFields(
  file: DiscoveredFile,
  docFile?: DataDoc["files"][number],
): Array<{ name: string; dtype: string }> {
  const columnsByName = new Map(
    file.columns.map((column) => [column.name.toLowerCase(), column]),
  );
  const priorityNames = [
    ...(docFile?.key_columns ?? []),
    ...(docFile?.measure_columns ?? []),
    ...(docFile?.date_columns ?? []),
    ...(docFile?.category_columns ?? []),
  ];
  const selected: Array<{ name: string; dtype: string }> = [];
  for (const name of priorityNames) {
    const column = columnsByName.get(name.toLowerCase());
    if (!column || selected.some((item) => item.name === column.name)) continue;
    selected.push({ name: column.name, dtype: column.dtype });
    if (selected.length >= 5) return selected;
  }
  const fallback = [...file.columns].sort((a, b) => {
    const aRank = fieldRank(a.name, a.dtype);
    const bRank = fieldRank(b.name, b.dtype);
    if (aRank !== bRank) return aRank - bRank;
    return a.name.localeCompare(b.name);
  });
  for (const column of fallback) {
    if (selected.some((item) => item.name === column.name)) continue;
    selected.push({ name: column.name, dtype: column.dtype });
    if (selected.length >= 5) return selected;
  }
  return selected;
}

function fieldRank(name: string, dtype: string): number {
  const normalized = name.toLowerCase();
  if (normalized === "id" || normalized.endsWith("_id")) return 0;
  if (/(amount|price|cost|value|total|balance|revenue)/.test(normalized)) {
    return 1;
  }
  if (dtype === "date" || dtype === "datetime") return 2;
  if (dtype === "category" || dtype === "bool") return 3;
  if (dtype === "int" || dtype === "float") return 4;
  return 5;
}

function friendlyFieldLabel(name: string, dtype: string): string {
  const normalized = name.toLowerCase();
  if (normalized === "id" || normalized.endsWith("_id")) return "ID";
  if (/(amount|price|cost|value|total|balance|revenue)/.test(normalized)) {
    return "amount";
  }
  if (dtype === "date" || dtype === "datetime") return "date";
  if (dtype === "int" || dtype === "float") return "number";
  if (dtype === "bool") return "yes/no";
  if (dtype === "category") return "category";
  return "text";
}

function inferGrain(file: DiscoveredFile): string {
  const label = humanizeLabel(file.friendly_name).toLowerCase();
  const unit = singularize(label || "record");
  return `One row per ${unit}.`;
}

function joinReadable(items: string[], numberSystem: NumberSystem): string {
  if (items.length === 0) return "the uploaded files";
  if (items.length === 1) return items[0]!;
  if (items.length === 2) return `${items[0]} and ${items[1]}`;
  const visible = items.slice(0, 3);
  const hiddenCount = items.length - visible.length;
  if (hiddenCount <= 0) {
    return `${visible.slice(0, -1).join(", ")}, and ${visible.at(-1)}`;
  }
  return `${visible.join(", ")}, and ${formatNumberValue(
    hiddenCount,
    numberSystem,
  )} more`;
}

function humanizeLabel(value: string): string {
  return value
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function singularize(value: string): string {
  if (value.endsWith("ies")) return `${value.slice(0, -3)}y`;
  if (value.endsWith("ses")) return value.slice(0, -2);
  if (value.endsWith("s") && value.length > 3) return value.slice(0, -1);
  return value;
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

function needsReview(
  confidence: number | undefined,
  reasons: string[] | undefined,
): boolean {
  return reviewReasons(confidence, reasons).length > 0;
}

function reviewReasons(
  confidence: number | undefined,
  reasons: string[] | undefined,
): string[] {
  if (reasons && reasons.length > 0) return reasons;
  if (typeof confidence === "number" && confidence < CONFIDENCE_REVIEW_THRESHOLD) {
    return ["low_confidence"];
  }
  return [];
}

function friendlyReason(reason: string): string {
  const known: Record<string, string> = {
    ambiguous_date: "Date order is ambiguous.",
    canonicalize_low_confidence: "Some values did not cleanly match the detected type.",
    header_low_confidence: "The detected header may need review.",
    invalid_header: "The field names look incomplete.",
    link_low_confidence: "The connection is not strong enough to confirm.",
    low_confidence: "Cerno was not confident enough to confirm this.",
    manual_field_added: "This field was added manually.",
    type_validation_failed: "The field type does not match enough values.",
  };
  if (known[reason]) return known[reason]!;
  if (reason.includes(" ") && /[.!?]$/.test(reason)) return reason;
  if (reason.includes(" ")) return `${reason}.`;
  const normalized = reason.replace(/_/g, " ");
  return `${normalized.charAt(0).toUpperCase()}${normalized.slice(1)}.`;
}

function formatSize(
  bytes: number | null | undefined,
  numberSystem: NumberSystem,
): string {
  if (typeof bytes !== "number") return "Unknown size";
  if (bytes < 1024) return `${formatNumberValue(bytes, numberSystem)} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = units[0]!;
  for (let i = 1; i < units.length && value >= 1024; i += 1) {
    value /= 1024;
    unit = units[i]!;
  }
  const rounded = value >= 10 ? Math.round(value) : Math.round(value * 10) / 10;
  return `${formatNumberValue(rounded, numberSystem)} ${unit}`;
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

function formatCell(value: unknown, numberSystem: NumberSystem): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number") return formatNumberValue(value, numberSystem);
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
