import { useEffect, useMemo, useRef, useState } from "react";
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
  SimpleDtype,
} from "../lib/types";
import { Modal } from "./Modal";

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

export function SchemaTab({
  files,
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
  const [previewFileId, setPreviewFileId] = useState<string | null>(null);
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
    if (!previewFileId) return;
    setPreview(null);
    setPreviewError(null);
    setPreviewLoading(true);
    getFilePreview(previewFileId, 100)
      .then((p) => setPreview(p))
      .catch((err: Error) => setPreviewError(err.message))
      .finally(() => setPreviewLoading(false));
  }, [previewFileId]);

  const fileNameMap = useMemo(() => {
    const m = new Map<string, string>();
    for (const f of files) m.set(f.id, f.friendly_name || f.filename);
    for (const df of draftFiles) {
      const friendly = df.friendly_name?.trim();
      if (friendly) m.set(df.file_id, friendly);
    }
    return m;
  }, [files, draftFiles]);

  const previewFile = files.find((f) => f.id === previewFileId) ?? null;

  const status = discovery?.status ?? "empty";

  const handleStartEdit = () => {
    setEditing(true);
  };
  const handleCancelEdit = () => {
    if (discovery) {
      setDraftFiles(discovery.files);
      setDraftLinks(discovery.links);
      setDraftOverview(discovery.overview);
    }
    setEditing(false);
  };
  const handleSave = () => {
    onApprove(draftFiles, draftLinks, draftOverview);
    setEditing(false);
  };
  const handleApproveAsIs = () => {
    onApprove(draftFiles, draftLinks, draftOverview);
  };

  const updateFile = (idx: number, patch: Partial<DiscoveredFile>) => {
    setDraftFiles((prev) =>
      prev.map((f, i) => (i === idx ? { ...f, ...patch } : f)),
    );
  };
  const updateColumn = (
    fileIdx: number,
    colIdx: number,
    patch: Partial<DiscoveredColumn>,
  ) => {
    setDraftFiles((prev) =>
      prev.map((f, i) => {
        if (i !== fileIdx) return f;
        return {
          ...f,
          columns: f.columns.map((c, j) =>
            j === colIdx ? { ...c, ...patch } : c,
          ),
        };
      }),
    );
  };
  const removeColumn = (fileIdx: number, colIdx: number) => {
    setDraftFiles((prev) =>
      prev.map((f, i) =>
        i === fileIdx
          ? { ...f, columns: f.columns.filter((_, j) => j !== colIdx) }
          : f,
      ),
    );
  };
  const addColumn = (fileIdx: number) => {
    setDraftFiles((prev) =>
      prev.map((f, i) =>
        i === fileIdx
          ? {
              ...f,
              columns: [
                ...f.columns,
                {
                  column_id: `col_${f.columns.length + 1}`,
                  name: "new_column",
                  description: "",
                  dtype: "string",
                },
              ],
            }
          : f,
      ),
    );
  };

  const updateLink = (idx: number, patch: Partial<DiscoveredLink>) => {
    setDraftLinks((prev) =>
      prev.map((l, i) => (i === idx ? { ...l, ...patch } : l)),
    );
  };
  const removeLink = (idx: number) => {
    setDraftLinks((prev) => prev.filter((_, i) => i !== idx));
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

  return (
    <div className="px-8 py-6">
      <header className="hairline mb-4 flex flex-wrap items-center justify-between gap-3 border-b pb-3">
        <div>
          <div className="small-caps text-xs text-neutral-500">schema</div>
          <h2 className="font-mono text-xl text-ink">
            {status === "approved"
              ? "approved"
              : status === "pending_review"
                ? "review draft"
                : status === "discovering"
                  ? "discovering\u2026"
                  : status === "failed"
                    ? "discovery failed"
                    : "no schema yet"}
          </h2>
        </div>
        <div className="flex items-center gap-2">
          {status === "empty" || status === "failed" ? (
            <button
              type="button"
              onClick={onProcess}
              disabled={!canProcess || processing}
              className="small-caps border border-ink bg-ember px-3 py-1.5 text-xs text-white hover:bg-ember-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              {processing ? "processing\u2026" : "+ process files"}
            </button>
          ) : null}
          {status === "pending_review" || status === "approved" ? (
            <>
              {editing ? (
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
                    onClick={handleSave}
                    disabled={approving}
                    className="small-caps border border-ink bg-ember px-3 py-1.5 text-xs text-white hover:bg-ember-hover disabled:opacity-40"
                  >
                    {approving ? "saving\u2026" : "save & apply"}
                  </button>
                </>
              ) : (
                <>
                  <button
                    type="button"
                    onClick={handleStartEdit}
                    className="small-caps border border-ink px-3 py-1.5 text-xs hover:bg-neutral-100"
                  >
                    edit
                  </button>
                  {status === "pending_review" ? (
                    <button
                      type="button"
                      onClick={handleApproveAsIs}
                      disabled={approving}
                      className="small-caps border border-ink bg-ember px-3 py-1.5 text-xs text-white hover:bg-ember-hover disabled:opacity-40"
                    >
                      {approving ? "approving\u2026" : "approve"}
                    </button>
                  ) : (
                    <button
                      type="button"
                      onClick={onProcess}
                      disabled={processing}
                      className="small-caps border border-ink px-3 py-1.5 text-xs hover:bg-neutral-100 disabled:opacity-40"
                    >
                      {processing ? "re-processing\u2026" : "re-process"}
                    </button>
                  )}
                </>
              )}
            </>
          ) : null}
        </div>
      </header>

      {processing || events.length > 0 ? (
        <ProgressLog events={events} processing={processing} />
      ) : null}

      {status === "empty" && !processing ? (
        <div className="mt-8 max-w-lg text-sm text-neutral-600">
          {canProcess
            ? "No schema yet. Click \u201c+ process files\u201d to have the model read the first rows of each file and identify headers, types, and relationships."
            : "Upload one or more files first, then process them."}
        </div>
      ) : null}

      {discovery && (status === "pending_review" || status === "approved") ? (
        <>
          <ModelView
            draftFiles={draftFiles}
            draftLinks={draftLinks}
            editing={editing}
            fileNameMap={fileNameMap}
            onOpenPreview={(fid) => setPreviewFileId(fid)}
            onUpdateFile={updateFile}
            onUpdateColumn={updateColumn}
            onRemoveColumn={removeColumn}
            onAddColumn={addColumn}
          />

          <section className="mt-8">
            <header className="hairline flex items-baseline justify-between border-b pb-2">
              <h3 className="small-caps text-sm text-ink">links</h3>
              {editing ? (
                <button
                  type="button"
                  onClick={addLink}
                  disabled={draftFiles.length < 2}
                  className="small-caps border border-ink px-2 py-1 text-[11px] hover:bg-neutral-100 disabled:opacity-40"
                >
                  + add link
                </button>
              ) : null}
            </header>
            {draftLinks.length === 0 ? (
              <div className="mt-3 text-xs text-neutral-500">
                No cross-file links detected.
              </div>
            ) : (
              <table className="mt-2 w-full border-collapse text-xs">
                <thead>
                  <tr>
                    <Th>from</Th>
                    <Th>to</Th>
                    <Th>direction</Th>
                    <Th>summary</Th>
                    {editing ? <Th>{""}</Th> : null}
                  </tr>
                </thead>
                <tbody>
                  {draftLinks.map((link, i) => (
                    <tr key={i} className="hairline border-b">
                      <Td mono>
                        <ColumnPicker
                          editing={editing}
                          fileId={link.file_a_id}
                          col={link.col_a}
                          draftFiles={draftFiles}
                          onChange={(fileId, col) =>
                            updateLink(i, { file_a_id: fileId, col_a: col })
                          }
                          fileNameMap={fileNameMap}
                        />
                      </Td>
                      <Td mono>
                        <ColumnPicker
                          editing={editing}
                          fileId={link.file_b_id}
                          col={link.col_b}
                          draftFiles={draftFiles}
                          onChange={(fileId, col) =>
                            updateLink(i, { file_b_id: fileId, col_b: col })
                          }
                          fileNameMap={fileNameMap}
                        />
                      </Td>
                      <Td>
                        {editing ? (
                          <select
                            value={link.direction}
                            onChange={(e) =>
                              updateLink(i, {
                                direction: e.target.value as LinkDirection,
                              })
                            }
                            className="small-caps border border-ink bg-paper px-1 py-0.5 text-[11px]"
                          >
                            {DIRECTIONS.map((d) => (
                              <option key={d} value={d}>
                                {d.replace(/_/g, " ")}
                              </option>
                            ))}
                          </select>
                        ) : (
                          <span className="small-caps text-[11px] text-neutral-500">
                            {link.direction.replace(/_/g, " ")}
                          </span>
                        )}
                      </Td>
                      <Td>
                        {editing ? (
                          <input
                            type="text"
                            value={link.summary}
                            onChange={(e) =>
                              updateLink(i, { summary: e.target.value })
                            }
                            className="w-full border border-ink bg-paper px-1 py-0.5 text-xs"
                          />
                        ) : (
                          <span className="text-xs">{link.summary}</span>
                        )}
                      </Td>
                      {editing ? (
                        <Td>
                          <button
                            type="button"
                            onClick={() => removeLink(i)}
                            className="small-caps text-[11px] text-red-600 hover:underline"
                          >
                            remove
                          </button>
                        </Td>
                      ) : null}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>

          <section className="mt-8">
            <header className="hairline flex items-baseline justify-between border-b pb-2">
              <h3 className="small-caps text-sm text-ink">overview</h3>
            </header>
            {editing ? (
              <textarea
                value={draftOverview}
                onChange={(e) => setDraftOverview(e.target.value)}
                rows={4}
                className="mt-3 w-full border border-ink bg-paper p-2 text-sm"
              />
            ) : (
              <p className="mt-3 max-w-3xl text-sm text-neutral-700">
                {draftOverview || (
                  <span className="text-neutral-400">No overview written.</span>
                )}
              </p>
            )}
          </section>
        </>
      ) : null}

      <Modal
        open={previewFileId !== null}
        onClose={() => setPreviewFileId(null)}
      >
        <div className="w-[640px] max-w-[80vw]">
          <header className="hairline mb-3 border-b pb-2">
            <div className="small-caps text-xs text-neutral-500">preview</div>
            <h3 className="font-mono text-sm text-ink">
              {previewFile?.friendly_name || previewFile?.filename}
            </h3>
          </header>
          {previewLoading ? (
            <div className="text-xs text-neutral-500">loading{"\u2026"}</div>
          ) : previewError ? (
            <div className="text-xs text-red-600">{previewError}</div>
          ) : preview ? (
            <PreviewTable preview={preview} />
          ) : null}
        </div>
      </Modal>
    </div>
  );
}

type ModelViewProps = {
  draftFiles: DiscoveredFile[];
  draftLinks: DiscoveredLink[];
  editing: boolean;
  fileNameMap: Map<string, string>;
  onOpenPreview: (fileId: string) => void;
  onUpdateFile: (idx: number, patch: Partial<DiscoveredFile>) => void;
  onUpdateColumn: (
    fileIdx: number,
    colIdx: number,
    patch: Partial<DiscoveredColumn>,
  ) => void;
  onRemoveColumn: (fileIdx: number, colIdx: number) => void;
  onAddColumn: (fileIdx: number) => void;
};

function ModelView({
  draftFiles,
  draftLinks,
  editing,
  fileNameMap,
  onOpenPreview,
  onUpdateFile,
  onUpdateColumn,
  onRemoveColumn,
  onAddColumn,
}: ModelViewProps) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const colRefs = useRef<Map<string, HTMLElement>>(new Map());
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const onResize = () => setTick((t) => t + 1);
    window.addEventListener("resize", onResize);
    const id = window.setTimeout(onResize, 50);
    return () => {
      window.removeEventListener("resize", onResize);
      window.clearTimeout(id);
    };
  }, [draftFiles, draftLinks, editing]);

  const linePoints = useMemo(() => {
    void tick;
    const root = containerRef.current;
    if (!root) return [];
    const rootRect = root.getBoundingClientRect();
    const out: { x1: number; y1: number; x2: number; y2: number; key: string }[] = [];
    draftLinks.forEach((link, i) => {
      const aKey = `${link.file_a_id}::${link.col_a}`;
      const bKey = `${link.file_b_id}::${link.col_b}`;
      const a = colRefs.current.get(aKey);
      const b = colRefs.current.get(bKey);
      if (!a || !b) return;
      const ar = a.getBoundingClientRect();
      const br = b.getBoundingClientRect();
      const ax = ar.left + ar.width / 2 - rootRect.left;
      const ay = ar.top + ar.height / 2 - rootRect.top;
      const bx = br.left + br.width / 2 - rootRect.left;
      const by = br.top + br.height / 2 - rootRect.top;
      out.push({ x1: ax, y1: ay, x2: bx, y2: by, key: `${i}` });
    });
    return out;
  }, [draftLinks, tick]);

  const setColRef = (key: string) => (el: HTMLElement | null) => {
    if (el) colRefs.current.set(key, el);
    else colRefs.current.delete(key);
  };

  return (
    <div ref={containerRef} className="relative">
      <svg
        className="pointer-events-none absolute inset-0 h-full w-full"
        style={{ overflow: "visible" }}
      >
        {linePoints.map((p) => (
          <line
            key={p.key}
            x1={p.x1}
            y1={p.y1}
            x2={p.x2}
            y2={p.y2}
            stroke="#c2410c"
            strokeWidth={1}
            opacity={0.6}
          />
        ))}
      </svg>
      <div className="relative grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-3">
        {draftFiles.map((file, fileIdx) => (
          <div
            key={file.file_id}
            className="hairline border bg-paper"
          >
            <header className="hairline border-b bg-neutral-50 px-3 py-2">
              {editing ? (
                <input
                  type="text"
                  value={file.friendly_name}
                  onChange={(e) =>
                    onUpdateFile(fileIdx, { friendly_name: e.target.value })
                  }
                  className="w-full bg-transparent font-mono text-sm text-ink focus:outline-none"
                />
              ) : (
                <button
                  type="button"
                  onClick={() => onOpenPreview(file.file_id)}
                  className="block w-full truncate text-left font-mono text-sm text-ink hover:text-ember"
                  title="preview rows"
                >
                  {file.friendly_name || fileNameMap.get(file.file_id)}
                </button>
              )}
              <div className="mt-1 flex items-center gap-3 text-[10px] text-neutral-500">
                <span className="small-caps">
                  header row{" "}
                  {editing ? (
                    <input
                      type="number"
                      value={file.header_row}
                      min={0}
                      onChange={(e) =>
                        onUpdateFile(fileIdx, {
                          header_row: Math.max(0, Number(e.target.value) || 0),
                        })
                      }
                      className="ml-1 w-12 border border-ink bg-paper px-1 text-[10px]"
                    />
                  ) : (
                    file.header_row
                  )}
                </span>
                <button
                  type="button"
                  onClick={() => onOpenPreview(file.file_id)}
                  className="small-caps hover:text-ember"
                >
                  preview
                </button>
              </div>
            </header>
            {editing ? (
              <textarea
                value={file.description}
                onChange={(e) =>
                  onUpdateFile(fileIdx, { description: e.target.value })
                }
                rows={2}
                placeholder="description\u2026"
                className="w-full border-b border-neutral-200 bg-paper px-3 py-2 text-xs"
              />
            ) : file.description ? (
              <div className="border-b border-neutral-200 px-3 py-2 text-xs text-neutral-600">
                {file.description}
              </div>
            ) : null}
            <ul>
              {file.columns.map((col, colIdx) => (
                <li
                  key={`${col.column_id}-${colIdx}`}
                  ref={setColRef(`${file.file_id}::${col.name}`)}
                  className="hairline flex items-center justify-between gap-2 border-b border-neutral-100 px-3 py-1.5 text-xs"
                >
                  {editing ? (
                    <>
                      <input
                        type="text"
                        value={col.name}
                        onChange={(e) =>
                          onUpdateColumn(fileIdx, colIdx, {
                            name: e.target.value,
                          })
                        }
                        className="w-32 border border-ink bg-paper px-1 py-0.5 font-mono text-xs"
                      />
                      <input
                        type="text"
                        value={col.description}
                        placeholder="description"
                        onChange={(e) =>
                          onUpdateColumn(fileIdx, colIdx, {
                            description: e.target.value,
                          })
                        }
                        className="flex-1 border border-ink bg-paper px-1 py-0.5 text-[11px]"
                      />
                      <select
                        value={col.dtype}
                        onChange={(e) =>
                          onUpdateColumn(fileIdx, colIdx, {
                            dtype: e.target.value as SimpleDtype,
                          })
                        }
                        className="small-caps border border-ink bg-paper px-1 py-0.5 text-[11px]"
                      >
                        {DTYPES.map((d) => (
                          <option key={d} value={d}>
                            {d}
                          </option>
                        ))}
                      </select>
                      <button
                        type="button"
                        onClick={() => onRemoveColumn(fileIdx, colIdx)}
                        className="small-caps text-[11px] text-red-600 hover:underline"
                      >
                        x
                      </button>
                    </>
                  ) : (
                    <>
                      <span className="truncate font-mono text-ink">
                        {col.name}
                      </span>
                      <span className="flex items-center gap-2">
                        {col.description ? (
                          <span className="max-w-[14rem] truncate text-[11px] text-neutral-500">
                            {col.description}
                          </span>
                        ) : null}
                        <span className="small-caps text-[11px] text-neutral-500">
                          {col.dtype}
                        </span>
                      </span>
                    </>
                  )}
                </li>
              ))}
            </ul>
            {editing ? (
              <div className="px-3 py-2">
                <button
                  type="button"
                  onClick={() => onAddColumn(fileIdx)}
                  className="small-caps text-[11px] text-neutral-500 hover:text-ember"
                >
                  + add column
                </button>
              </div>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}

function ColumnPicker({
  editing,
  fileId,
  col,
  draftFiles,
  fileNameMap,
  onChange,
}: {
  editing: boolean;
  fileId: string;
  col: string;
  draftFiles: DiscoveredFile[];
  fileNameMap: Map<string, string>;
  onChange: (fileId: string, col: string) => void;
}) {
  const file = draftFiles.find((f) => f.file_id === fileId);
  if (!editing) {
    const fname = fileNameMap.get(fileId) ?? fileId;
    return (
      <span>
        {fname}.{col}
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1">
      <select
        value={fileId}
        onChange={(e) => {
          const next = draftFiles.find((f) => f.file_id === e.target.value);
          onChange(e.target.value, next?.columns[0]?.name ?? "");
        }}
        className="border border-ink bg-paper px-1 py-0.5 text-[11px]"
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
        className="border border-ink bg-paper px-1 py-0.5 text-[11px]"
      >
        {(file?.columns ?? []).map((c) => (
          <option key={c.column_id || c.name} value={c.name}>
            {c.name}
          </option>
        ))}
      </select>
    </span>
  );
}

function ProgressLog({
  events,
  processing,
}: {
  events: ProcessingEvent[];
  processing: boolean;
}) {
  return (
    <section className="mb-6 border border-neutral-200 bg-neutral-50 p-3">
      <div className="small-caps text-[10px] text-neutral-500">progress</div>
      <ul className="mt-2 space-y-1">
        {events.map((e) => (
          <li key={e.id} className="flex items-baseline gap-2 font-mono text-[11px]">
            <span className="text-neutral-400">
              {new Date(e.created_at).toLocaleTimeString()}
            </span>
            <span
              className={
                e.kind === "error"
                  ? "text-red-600"
                  : e.kind === "done"
                    ? "text-ember"
                    : "text-ink"
              }
            >
              {e.kind}
            </span>
            <span className="text-neutral-600">{e.message}</span>
          </li>
        ))}
        {processing && events.length === 0 ? (
          <li className="font-mono text-[11px] text-neutral-500">starting{"\u2026"}</li>
        ) : null}
      </ul>
    </section>
  );
}

function PreviewTable({ preview }: { preview: FilePreviewResponse }) {
  return (
    <div className="max-h-[60vh] overflow-auto">
      <table className="w-full border-collapse text-xs">
        <thead className="sticky top-0 bg-paper">
          <tr>
            {preview.columns.map((c) => (
              <th
                key={c}
                className="hairline border-b px-2 py-1 text-left font-mono text-[11px] text-ink"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {preview.rows.map((row, i) => (
            <tr key={i} className="hairline border-b">
              {preview.columns.map((_, j) => (
                <td key={j} className="px-2 py-1 font-mono text-[11px]">
                  {formatCell(row[j])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="small-caps mt-3 text-[10px] text-neutral-500">
        showing {preview.rows.length} of {preview.total_rows.toLocaleString()} rows
      </div>
    </div>
  );
}

function formatCell(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number") return String(v);
  if (typeof v === "boolean") return v ? "true" : "false";
  return JSON.stringify(v);
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="small-caps hairline border-b px-2 py-1.5 text-left text-[10px] text-neutral-500">
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
    <td className={`px-2 py-1.5 align-middle ${mono ? "font-mono" : ""}`}>
      {children}
    </td>
  );
}
