import { useEffect, useMemo, useState, type ReactNode } from "react";
import { getFilePreview } from "../lib/api";
import type {
  DataDoc,
  DataDocFile,
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
    label: "Preparing files",
    kinds: ["queued", "uploading", "ingesting_file", "started", "loading_artifacts", "reading_files"],
    note: "Uploading, loading, and reading sheets, headers, and usable rows.",
  },
  {
    label: "Finding meaning",
    kinds: ["python_analysis", "calling_llm", "parsing_response"],
    note: "Naming files, explaining columns, and checking how documents relate.",
  },
  {
    label: "Preparing review",
    kinds: ["saving_schema", "resolving_links", "done"],
    note: "Saving a draft map so you can inspect it.",
  },
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
    <div className="px-8 py-6">
      <header className="hairline mb-5 flex flex-wrap items-start justify-between gap-4 border-b pb-5">
        <div className="max-w-3xl">
          <div className="small-caps text-xs text-neutral-500">schema</div>
          <h2 className="mt-1 font-mono text-2xl text-ink">
            {schemaTitle(status, processing)}
          </h2>
          <p className="mt-2 text-sm leading-6 text-neutral-600">
            {headerDescription(status, processing, files.length)}
          </p>
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
                  {approving ? "saving" : "save and apply"}
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
          <section className="hairline border-b pb-5">
            <div className="small-caps text-xs text-neutral-500">what Cerno found</div>
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

          <DataHealthSummary summary={summary} warnings={warnings} />

          <section className="hairline border-b pb-5">
            <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
              <div>
                <div className="small-caps text-xs text-neutral-500">files</div>
                <h3 className="mt-1 font-mono text-lg text-ink">Uploaded documents</h3>
                <p className="mt-1 text-sm leading-5 text-neutral-600">
                  Each tile is one file, sheet, or table Cerno can use. Open a tile
                  only when you need column-level detail.
                </p>
              </div>
              <div className="text-xs text-neutral-500">
                Click a document to inspect it.
              </div>
            </div>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {draftFiles.map((file) => (
                <FileTile
                  key={file.file_id}
                  file={file}
                  record={fileById.get(file.file_id)}
                  displayName={fileNameMap.get(file.file_id) ?? file.friendly_name}
                  warnings={warnings.byFile.get(file.file_id) ?? []}
                  onClick={() => setActiveFileId(file.file_id)}
                />
              ))}
            </div>
          </section>

          <section className="hairline border-b pb-5">
            <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
              <div>
                <div className="small-caps text-xs text-neutral-500">connections</div>
                <h3 className="mt-1 font-mono text-lg text-ink">How the documents connect</h3>
                <p className="mt-1 text-sm leading-5 text-neutral-600">
                  These are the shared fields Cerno will use to move between files.
                  Review weak or missing connections before approving complex uploads.
                </p>
              </div>
              {editing ? (
                <button
                  type="button"
                  onClick={() => addLink()}
                  disabled={draftFiles.length < 2}
                  className="small-caps border border-ink px-2 py-1 text-[11px] hover:bg-neutral-100 disabled:opacity-40"
                >
                  add connection
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

          <DataGuideSection doc={doc} files={draftFiles} />
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
    return "Check the plain-language map before Cerno builds dashboards or answers questions from these files.";
  }
  if (status === "approved") {
    return "This map is the shared understanding Cerno uses for dashboards and chat.";
  }
  if (status === "failed") {
    return "Processing stopped before Cerno could finish the map. Fix the issue, then process the files again.";
  }
  if (fileCount > 0) {
    return "Process the uploaded files to find headers, meanings, and connections between documents.";
  }
  return "Upload one or more files, then Cerno can build a plain-language map of the dataset.";
}

function EmptyState({ canProcess }: { canProcess: boolean }) {
  return (
    <div className="mt-10 max-w-xl border border-neutral-200 bg-white p-5">
      <h3 className="font-mono text-base text-ink">Start with uploaded documents</h3>
      <p className="mt-2 text-sm leading-6 text-neutral-600">
        {canProcess
          ? "Cerno will identify headers, explain fields, and look for connections between files."
          : "Upload one or more files first, then Cerno can build the data map."}
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
  const latestEvent = events.at(-1) ?? null;
  const completedKinds = new Set(events.map((event) => event.kind));
  const currentPhase = completedKinds.has("done")
    ? 2
    : completedKinds.has("python_analysis") ||
        completedKinds.has("calling_llm") ||
        completedKinds.has("parsing_response")
      ? 1
      : 0;
  const progress =
    latestEvent?.progress ?? (completedKinds.has("done") ? 100 : currentPhase === 1 ? 58 : 24);
  const current = PHASES[currentPhase] ?? PHASES[0]!;

  return (
    <section className="mb-6 border border-neutral-200 bg-white p-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h3 className="font-mono text-sm text-ink">
            {latestError ? "Processing stopped" : current.label}
          </h3>
          <p className="mt-1 text-xs text-neutral-500">
            {latestError ? "Review the message below and try again." : current.note}
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
      {latestError ? (
        <div className="mt-4 border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
          {friendlyEventMessage(latestError)}
        </div>
      ) : null}
    </section>
  );
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

function DataHealthSummary({
  summary,
  warnings,
}: {
  summary: SummaryStats;
  warnings: WarningState;
}) {
  return (
    <section className="hairline border-b pb-5">
      <div className="grid gap-4 md:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        <div>
          <div className="small-caps text-xs text-neutral-500">review strip</div>
          <h3 className="mt-1 font-mono text-xl text-ink">Check before approving</h3>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-neutral-600">
            Start here for the shape of the upload. Open individual documents only
            when a warning or connection needs a closer look.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
          <Stat label="size" value={summary.size} />
          <Stat label="rows" value={summary.rows} />
          <Stat label="files" value={summary.files} />
          <Stat label="columns" value={summary.columns} />
          <Stat label="connections" value={summary.relationships} />
          <Stat label="state" value={summary.state} />
        </div>
      </div>
      {warnings.total > 0 ? (
        <div className="mt-4 border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
          {warnings.total} item{warnings.total === 1 ? "" : "s"} need a look. Open the
          marked documents before approving.
        </div>
      ) : null}
    </section>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="small-caps text-[11px] text-neutral-500">{label}</div>
      <div className="mt-1 font-mono text-lg text-ink">{value}</div>
    </div>
  );
}

function FileTile({
  file,
  record,
  displayName,
  warnings,
  onClick,
}: {
  file: DiscoveredFile;
  record?: FileRecord;
  displayName: string;
  warnings: string[];
  onClick: () => void;
}) {
  const badges = fileBadges(file, warnings);
  return (
    <button
      type="button"
      onClick={onClick}
      title={fileHoverTitle(file, record)}
      className="group min-h-[8rem] border border-neutral-200 bg-white p-4 text-left transition hover:border-ink hover:bg-neutral-50"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate font-mono text-base text-ink">{displayName}</div>
          <div className="mt-1 truncate text-xs text-neutral-500">
            {record?.filename ?? "Original file unknown"}
          </div>
        </div>
        <span
          className={`small-caps shrink-0 border px-1.5 py-0.5 text-[10px] ${
            warnings.length > 0
              ? "border-amber-200 bg-amber-50 text-amber-800"
              : "border-neutral-200 bg-neutral-50 text-neutral-500"
          }`}
        >
          {warnings.length > 0 ? "check" : "ready"}
        </span>
      </div>
      <p className="mt-3 max-h-10 overflow-hidden text-sm leading-5 text-neutral-600">
        {file.description || "No description yet."}
      </p>
      <div className="mt-4 flex flex-wrap gap-3 text-xs text-neutral-500">
        <span>{formatNumber(record?.row_count ?? 0)} rows</span>
        <span>{file.columns.length} columns</span>
        <span>{formatSize(record?.original_size_bytes)}</span>
      </div>
      <div className="mt-3 flex flex-wrap gap-1.5">
        {badges.map((badge) => (
          <span
            key={badge}
            className="small-caps border border-neutral-200 bg-neutral-50 px-1.5 py-0.5 text-[10px] text-neutral-600"
          >
            {badge}
          </span>
        ))}
      </div>
    </button>
  );
}

function DataGuideSection({
  doc,
  files,
}: {
  doc: DataDoc | null;
  files: DiscoveredFile[];
}) {
  if (!doc) {
    return (
      <section className="pb-5">
        <div className="small-caps text-xs text-neutral-500">guidance</div>
        <div className="mt-3 border border-neutral-200 bg-white p-4">
          <h3 className="font-mono text-base text-ink">Usage notes will appear here</h3>
          <p className="mt-2 max-w-2xl text-sm leading-6 text-neutral-600">
            Once the map is saved, Cerno will keep the plain-language notes,
            questions, and glossary on this Schema page.
          </p>
        </div>
      </section>
    );
  }

  const documentedIds = new Set(doc.files.map((file) => file.file_id));
  const missingGuides = files.filter((file) => !documentedIds.has(file.file_id));
  const caveatFiles = doc.files.filter((file) => file.caveats.length > 0);

  return (
    <section className="pb-5">
      <div className="mb-4 flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="small-caps text-xs text-neutral-500">guidance</div>
          <h3 className="mt-1 font-mono text-lg text-ink">How to use this data</h3>
          <p className="mt-1 max-w-3xl text-sm leading-5 text-neutral-600">
            These notes travel with the map, so the dashboard and chat
            share the same understanding of the uploaded documents.
          </p>
        </div>
        <div className="small-caps text-xs text-neutral-500">
          {doc.files.length} documented
        </div>
      </div>

      <div className="grid gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(18rem,0.8fr)]">
        <div className="space-y-4">
          <section className="border-y border-neutral-200">
            {doc.files.map((file) => (
              <DocumentNote key={file.file_id} file={file} />
            ))}
            {missingGuides.map((file) => (
              <div key={file.file_id} className="hairline border-b py-4 last:border-b-0">
                <div className="font-mono text-sm text-ink">{file.friendly_name}</div>
                <p className="mt-1 text-sm leading-5 text-neutral-600">
                  No usage note has been written for this document yet.
                </p>
              </div>
            ))}
          </section>

          <GuideList
            title="Questions to try"
            items={doc.starter_questions}
            empty="No starter questions yet."
          />
        </div>

        <aside className="space-y-6">
          <GuideList
            title="Watch-outs"
            items={[
              ...doc.usage_notes,
              ...caveatFiles.flatMap((file) =>
                file.caveats.map((caveat) => `${file.name}: ${caveat}`),
              ),
            ]}
            empty="No caveats yet."
          />

          <section>
            <div className="small-caps text-xs text-neutral-500">glossary</div>
            {doc.glossary.length === 0 ? (
              <p className="mt-3 text-sm text-neutral-500">No glossary terms yet.</p>
            ) : (
              <div className="mt-3 space-y-3">
                {doc.glossary.map((item) => (
                  <div key={item.term}>
                    <div className="font-mono text-sm text-ink">{item.term}</div>
                    <p className="mt-1 text-sm leading-5 text-neutral-600">
                      {item.meaning}
                    </p>
                  </div>
                ))}
              </div>
            )}
          </section>
        </aside>
      </div>
    </section>
  );
}

function DocumentNote({ file }: { file: DataDocFile }) {
  return (
    <article className="hairline border-b py-4 last:border-b-0">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="font-mono text-base text-ink">{file.name}</h4>
          <p className="mt-1 max-w-3xl text-sm leading-6 text-neutral-700">
            {file.description || "No description yet."}
          </p>
        </div>
        <div className="small-caps shrink-0 text-xs text-neutral-500">
          {formatNumber(file.row_count)} rows
        </div>
      </div>
      <div className="mt-3 grid gap-3 text-sm text-neutral-600 md:grid-cols-2 xl:grid-cols-4">
        <GuideFact label="One row means" value={file.grain || "Not documented"} />
        <GuideFact label="Key fields" value={joinItems(file.key_columns)} />
        <GuideFact label="Dates" value={joinItems(file.date_columns)} />
        <GuideFact label="Amounts" value={joinItems(file.measure_columns)} />
      </div>
    </article>
  );
}

function GuideFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="small-caps text-[11px] text-neutral-500">{label}</div>
      <div className="mt-1 leading-5">{value}</div>
    </div>
  );
}

function GuideList({
  title,
  items,
  empty,
}: {
  title: string;
  items: string[];
  empty: string;
}) {
  return (
    <section>
      <div className="small-caps text-xs text-neutral-500">{title}</div>
      {items.length === 0 ? (
        <p className="mt-3 text-sm text-neutral-500">{empty}</p>
      ) : (
        <ul className="mt-3 divide-y divide-neutral-100 border-y border-neutral-200 text-sm leading-5 text-neutral-700">
          {items.map((item, idx) => (
            <li key={`${idx}-${item}`} className="py-3">
              {item}
            </li>
          ))}
        </ul>
      )}
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

  if (groups.length === 0) {
    return (
      <div className="border border-neutral-200 bg-white p-4 text-sm text-neutral-600">
        No strong document-to-document connections yet. You can still approve the
        map, or add a connection in edit mode if you know how the files relate.
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {groups.map((group) => {
        const weakCount = group.links.filter((link) =>
          isWeakLink(linkStats.get(linkLookupKey(link.file_a_id, link.col_a, link.file_b_id, link.col_b))),
        ).length;
        return (
          <div
            key={group.key}
            className="grid items-center gap-3 border border-neutral-200 bg-white p-3 md:grid-cols-[minmax(0,1fr)_8rem_minmax(0,1fr)]"
          >
            <FileNode
              label={fileNameMap.get(group.leftId) ?? group.leftId}
              onClick={() => onSelectFile(group.leftId)}
            />
            <button
              type="button"
              onClick={() => onSelectFile(group.leftId)}
              className="group flex items-center justify-center gap-2 text-xs text-neutral-500"
              title="Open a file to inspect linked columns"
            >
              <span className="h-px flex-1 bg-neutral-300 group-hover:bg-ember" />
              <span className="small-caps whitespace-nowrap border border-neutral-200 px-2 py-1 text-[10px] text-neutral-600 group-hover:border-ember group-hover:text-ember">
                {group.links.length} connection{group.links.length === 1 ? "" : "s"}
              </span>
              <span className="h-px flex-1 bg-neutral-300 group-hover:bg-ember" />
            </button>
            <FileNode
              label={fileNameMap.get(group.rightId) ?? group.rightId}
              onClick={() => onSelectFile(group.rightId)}
            />
            {weakCount > 0 ? (
              <div className="md:col-span-3 text-xs text-amber-700">
                {weakCount} connection{weakCount === 1 ? "" : "s"} may need review.
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}

function FileNode({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="min-w-0 border border-neutral-200 bg-neutral-50 px-3 py-2 text-left hover:border-ink hover:bg-white"
    >
      <span className="block truncate font-mono text-sm text-ink">{label}</span>
    </button>
  );
}

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
}) {
  if (!open || !file || fileIdx < 0) return null;
  const fileLinks = links
    .map((link, idx) => ({ link, idx }))
    .filter(({ link }) => link.file_a_id === file.file_id || link.file_b_id === file.file_id);

  return (
    <div className="fixed inset-0 z-50 bg-black/20" onClick={onClose}>
      <aside
        className="absolute inset-y-0 right-0 flex w-full max-w-3xl flex-col border-l border-ink bg-paper shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="hairline flex items-start justify-between gap-4 border-b p-5">
          <div className="min-w-0">
            <div className="small-caps text-xs text-neutral-500">document detail</div>
            {editing ? (
              <input
                type="text"
                value={file.friendly_name}
                onChange={(e) => onUpdateFile(fileIdx, { friendly_name: e.target.value })}
                className="mt-2 w-full border border-neutral-300 bg-white px-2 py-1 font-mono text-xl focus:outline-none focus:ring-1 focus:ring-ink"
              />
            ) : (
              <h3 className="mt-1 truncate font-mono text-xl text-ink">
                {file.friendly_name}
              </h3>
            )}
            <div className="mt-2 flex flex-wrap gap-3 text-xs text-neutral-500">
              <span>{record?.filename ?? "Original file unknown"}</span>
              <span>{formatSize(record?.original_size_bytes)}</span>
              <span>{formatNumber(record?.row_count ?? 0)} rows</span>
              <span>{file.columns.length} columns</span>
              <span>header starts on row {file.header_row}</span>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="small-caps shrink-0 border border-ink px-2 py-1 text-xs hover:bg-neutral-100"
          >
            close
          </button>
        </div>

        <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-5">
          {warnings.length > 0 ? (
            <div className="border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
              {warnings.map((warning) => (
                <div key={warning}>{warning}</div>
              ))}
            </div>
          ) : null}

          <section>
            <div className="small-caps text-xs text-neutral-500">what this contains</div>
            {editing ? (
              <textarea
                value={file.description}
                onChange={(e) => onUpdateFile(fileIdx, { description: e.target.value })}
                rows={3}
                className="mt-2 w-full border border-neutral-300 bg-white p-2 text-sm focus:outline-none focus:ring-1 focus:ring-ink"
              />
            ) : (
              <p className="mt-2 text-sm leading-6 text-neutral-700">
                {file.description || "No description yet."}
              </p>
            )}
            {editing ? (
              <label className="mt-3 flex items-center gap-2 text-xs text-neutral-600">
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
                  className="w-20 border border-neutral-300 bg-white px-2 py-1"
                />
              </label>
            ) : null}
          </section>

          <section>
            <div className="mb-3 flex items-center justify-between gap-3">
              <div>
                <div className="small-caps text-xs text-neutral-500">columns</div>
                <h4 className="mt-1 font-mono text-base text-ink">Important fields</h4>
              </div>
              {editing ? (
                <button
                  type="button"
                  onClick={() => onAddColumn(fileIdx)}
                  className="small-caps border border-ink px-2 py-1 text-xs hover:bg-neutral-100"
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
                <div className="small-caps text-xs text-neutral-500">connections</div>
                <h4 className="mt-1 font-mono text-base text-ink">Related documents</h4>
              </div>
              {editing ? (
                <button
                  type="button"
                  onClick={() => onAddLink(file.file_id)}
                  disabled={draftFiles.length < 2}
                  className="small-caps border border-ink px-2 py-1 text-xs hover:bg-neutral-100 disabled:opacity-40"
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
            <div className="hairline flex items-center justify-between border-b pb-3">
              <div>
                <div className="small-caps text-xs text-neutral-500">preview</div>
                <h4 className="mt-1 font-mono text-base text-ink">Sample rows</h4>
              </div>
              {preview ? (
                <div className="small-caps text-xs text-neutral-500">
                  {formatNumber(preview.total_rows)} rows
                </div>
              ) : null}
            </div>
            {previewLoading ? (
              <div className="py-4 text-sm text-neutral-500">Loading preview...</div>
            ) : previewError ? (
              <div className="py-4 text-sm text-red-600">{previewError}</div>
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
    <div className="overflow-x-auto border border-neutral-200 bg-white">
      <table className="w-full min-w-[42rem] border-collapse text-sm">
        <thead className="bg-neutral-50">
          <tr>
            <Th>field</Th>
            <Th>meaning</Th>
            <Th>type</Th>
            {editing ? <Th>{""}</Th> : null}
          </tr>
        </thead>
        <tbody>
          {file.columns.map((col, colIdx) => (
            <tr key={`${col.column_id}-${colIdx}`} className="hairline border-b last:border-b-0">
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
                  col.description || <span className="text-amber-700">Needs description</span>
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
                  <span className="small-caps text-xs text-neutral-500">{col.dtype}</span>
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
      <div className="border border-neutral-200 bg-white p-4 text-sm text-neutral-500">
        No connections for this document.
      </div>
    );
  }

  return (
    <div className="divide-y divide-neutral-100 border border-neutral-200 bg-white">
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
                  className="border border-neutral-300 px-2 py-1 text-xs md:col-span-2"
                />
              </div>
            ) : (
              <>
                <div className="text-sm text-ink">
                  {relationshipSentence(link, fileNameMap)}
                </div>
                <div className="mt-2 flex flex-wrap gap-2 text-xs text-neutral-500">
                  <span className="small-caps border border-neutral-200 px-1.5 py-0.5 text-[10px]">
                    {link.direction.replace(/_/g, " ")}
                  </span>
                  <EvidenceLabel stat={stat} />
                </div>
                {link.summary ? (
                  <p className="mt-2 text-sm leading-5 text-neutral-600">{link.summary}</p>
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
    <div className="max-h-[22rem] overflow-auto border border-neutral-200 border-t-0 bg-white">
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
    <span className={pct < 70 ? "text-amber-700" : ""}>
      {pct}% match from {source}
    </span>
  );
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

function fileBadges(file: DiscoveredFile, warnings: string[]): string[] {
  const badges: string[] = [];
  const lowerNames = file.columns.map((column) => column.name.toLowerCase());
  if (file.columns.some((column) => column.dtype === "date" || column.dtype === "datetime")) {
    badges.push("has dates");
  }
  if (
    file.columns.some((column) => column.dtype === "int" || column.dtype === "float") ||
    lowerNames.some((name) =>
      ["amount", "price", "cost", "revenue", "total", "quantity"].some((term) =>
        name.includes(term),
      ),
    )
  ) {
    badges.push("has numbers");
  }
  if (
    lowerNames.some((name) =>
      ["id", "key", "code", "number"].some((term) => name.includes(term)),
    )
  ) {
    badges.push("has ids");
  }
  if (warnings.length > 0) badges.push("needs review");
  return badges.slice(0, 4);
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

function fileHoverTitle(file: DiscoveredFile, record?: FileRecord): string {
  const original = record?.filename ?? file.friendly_name;
  return `${original}\n${formatSize(record?.original_size_bytes)}\n${formatNumber(record?.row_count ?? 0)} rows`;
}

function cleanFilename(filename: string): string {
  const stem = filename.replace(/\.[^.]+$/, "");
  return stem.replace(/[_-]+/g, " ").replace(/\s+/g, " ").trim() || filename;
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

function joinItems(items: string[]): string {
  return items.length > 0 ? items.join(", ") : "Not documented";
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

function Th({ children }: { children: ReactNode }) {
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
  children: ReactNode;
  mono?: boolean;
}) {
  return (
    <td className={`px-3 py-2 align-top text-xs ${mono ? "font-mono" : ""}`}>
      {children}
    </td>
  );
}
