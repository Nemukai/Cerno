import { useEffect, useRef } from "react";
import type {
  DashboardPage,
  DiscoveryStatus,
  FileRecord,
  NotebookCell,
} from "../lib/types";
import { widgetFromCell } from "../lib/widgets";
import { WidgetRenderer } from "./WidgetRenderer";

type Props = {
  pages: DashboardPage[];
  cellsByPage: Record<string, NotebookCell[]>;
  focusPageId: string | null;
  files: FileRecord[];
  discoveryStatus: DiscoveryStatus;
  onUpload: (files: File[]) => void;
  uploading: boolean;
  onDeleteFile: (fileId: string) => void;
  onProcess: () => void;
  processing: boolean;
  onBuildDashboard: () => void;
  building: boolean;
  onReviewSchema: () => void;
};

export function DashboardTab({
  pages,
  cellsByPage,
  focusPageId,
  files,
  discoveryStatus,
  onUpload,
  uploading,
  onDeleteFile,
  onProcess,
  processing,
  onBuildDashboard,
  building,
  onReviewSchema,
}: Props) {
  const pageRefs = useRef<Record<string, HTMLElement | null>>({});

  useEffect(() => {
    if (!focusPageId) return;
    const node = pageRefs.current[focusPageId];
    if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [focusPageId]);

  if (pages.length === 0) {
    return (
      <EmptyStage
        files={files}
        discoveryStatus={discoveryStatus}
        onUpload={onUpload}
        uploading={uploading}
        onDeleteFile={onDeleteFile}
        onProcess={onProcess}
        processing={processing}
        onBuildDashboard={onBuildDashboard}
        building={building}
        onReviewSchema={onReviewSchema}
      />
    );
  }

  return (
    <div className="px-8 py-6">
      {pages.map((page) => {
        const cells = cellsByPage[page.id] ?? [];
        const widgets = cells
          .map(widgetFromCell)
          .filter((w) => w !== null);
        const kpis = widgets.filter((widget) => widget.kind === "kpi");
        const visuals = widgets.filter((widget) => widget.kind !== "kpi");
        const num = String(page.position + 1).padStart(2, "0");
        return (
          <section
            key={page.id}
            ref={(el) => {
              pageRefs.current[page.id] = el;
            }}
            className="mb-10"
          >
            <header className="hairline border-b pb-2">
              <h2 className="small-caps text-sm text-ink">
                {num} {page.title}
              </h2>
            </header>
            {widgets.length === 0 ? (
              <div className="mt-4 text-xs text-neutral-500">
                No widgets on this page.
              </div>
            ) : (
              <>
                {kpis.length > 0 ? (
                  <div className="grid gap-x-10 border-b border-neutral-200 md:grid-cols-2 xl:grid-cols-4">
                    {kpis.map((widget, i) => (
                      <WidgetRenderer key={`${widget.title}-${i}`} widget={widget} />
                    ))}
                  </div>
                ) : null}
                <div className="grid grid-cols-1 gap-x-10 md:grid-cols-2">
                  {visuals.map((widget, i) => (
                    <div key={`${widget.title}-${i}`} className="hairline border-b">
                      <WidgetRenderer widget={widget} />
                    </div>
                  ))}
                </div>
              </>
            )}
          </section>
        );
      })}
    </div>
  );
}

type EmptyProps = {
  files: FileRecord[];
  discoveryStatus: DiscoveryStatus;
  onUpload: (files: File[]) => void;
  uploading: boolean;
  onDeleteFile: (fileId: string) => void;
  onProcess: () => void;
  processing: boolean;
  onBuildDashboard: () => void;
  building: boolean;
  onReviewSchema: () => void;
};

type Stage = "upload" | "process" | "review" | "build" | "discovering" | "failed";

function pickStage(files: FileRecord[], status: DiscoveryStatus, processing: boolean): Stage {
  if (files.length === 0) return "upload";
  if (processing || status === "discovering") return "discovering";
  if (status === "pending_review") return "review";
  if (status === "approved") return "build";
  if (status === "failed") return "failed";
  return "process";
}

function EmptyStage({
  files,
  discoveryStatus,
  onUpload,
  uploading,
  onDeleteFile,
  onProcess,
  processing,
  onBuildDashboard,
  building,
  onReviewSchema,
}: EmptyProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const handlePick = () => inputRef.current?.click();
  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const fs = Array.from(e.target.files ?? []);
    if (fs.length > 0) onUpload(fs);
    e.target.value = "";
  };

  const stage = pickStage(files, discoveryStatus, processing);

  const labels: Record<Stage, { num: string; title: string }> = {
    upload: { num: "01", title: "add your files" },
    process: { num: "02", title: "discover the schema" },
    discovering: { num: "02", title: "discovering schema\u2026" },
    failed: { num: "02", title: "discovery failed" },
    review: { num: "03", title: "review the schema" },
    build: { num: "04", title: "build the overview" },
  };

  const { num, title } = labels[stage];

  return (
    <div className="px-8 py-10">
      <div className="small-caps text-xs text-neutral-500">{num}</div>
      <h2 className="mt-1 font-mono text-2xl tracking-tight text-ink">
        {title}
      </h2>

      <input
        ref={inputRef}
        type="file"
        accept=".csv,.xlsx,.xls"
        multiple
        className="hidden"
        onChange={handleChange}
      />

      {stage === "upload" ? (
        <>
          <p className="mt-4 max-w-lg text-sm text-neutral-600">
            Upload CSV or XLSX files. You can add them one at a time or all at
            once; drop files anywhere in the window or choose them below.
          </p>
          <button
            type="button"
            onClick={handlePick}
            disabled={uploading}
            onDragOver={(e) => {
              e.preventDefault();
              e.stopPropagation();
            }}
            onDrop={(e) => {
              e.preventDefault();
              e.stopPropagation();
              const fs = Array.from(e.dataTransfer.files ?? []);
              if (fs.length > 0) onUpload(fs);
            }}
            className="mt-6 block w-full max-w-lg cursor-pointer border border-dashed border-neutral-300 p-10 text-center text-xs text-neutral-500 hover:border-ink disabled:cursor-not-allowed disabled:opacity-40"
          >
            {uploading ? "uploading\u2026" : "drop files here or click to choose"}
          </button>
        </>
      ) : null}

      {stage === "process" || stage === "failed" ? (
        <>
          <p className="mt-4 max-w-lg text-sm text-neutral-600">
            {files.length} file{files.length === 1 ? "" : "s"} uploaded. Cerno
            will read the first rows of each file and ask the model to identify
            headers, types, and cross-file relationships.
          </p>
          <ul className="mt-4 max-w-lg font-mono text-xs text-ink">
            {files.map((f) => (
              <li
                key={f.id}
                className="hairline flex items-baseline justify-between gap-3 border-b py-1.5"
              >
                <span className="min-w-0 flex-1 truncate">{f.filename}</span>
                <span className="text-neutral-500">
                  {f.row_count.toLocaleString()} rows
                </span>
                <button
                  type="button"
                  onClick={() => {
                    if (window.confirm(`Remove ${f.filename}?`)) {
                      onDeleteFile(f.id);
                    }
                  }}
                  className="small-caps text-[11px] text-neutral-400 hover:text-red-600"
                  title="remove this file"
                >
                  remove
                </button>
              </li>
            ))}
          </ul>
          {stage === "failed" ? (
            <p className="mt-3 max-w-lg text-xs text-red-600">
              The previous discovery run failed. Check the schema tab for details
              or try again.
            </p>
          ) : null}
          <div className="mt-6 flex items-center gap-3">
            <button
              type="button"
              onClick={handlePick}
              disabled={uploading}
              className="small-caps border border-ink px-3 py-1.5 text-xs hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {uploading ? "uploading\u2026" : "+ add more files"}
            </button>
            <button
              type="button"
              onClick={onProcess}
              disabled={processing}
              className="small-caps border border-ink bg-ember px-3 py-1.5 text-xs text-white hover:bg-ember-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              {stage === "failed" ? "+ retry process" : "+ process files"}
            </button>
          </div>
        </>
      ) : null}

      {stage === "discovering" ? (
        <>
          <p className="mt-4 max-w-lg text-sm text-neutral-600">
            The model is reading your files. This can take a minute or two with
            high reasoning. Open the Schema tab to follow progress.
          </p>
          <div className="mt-6">
            <button
              type="button"
              onClick={onReviewSchema}
              className="small-caps border border-ink px-3 py-1.5 text-xs hover:bg-neutral-100"
            >
              open schema tab
            </button>
          </div>
        </>
      ) : null}

      {stage === "review" ? (
        <>
          <p className="mt-4 max-w-lg text-sm text-neutral-600">
            The model has produced a draft schema. Review the columns and links
            in the Schema tab, then approve to apply types and cast the data.
          </p>
          <div className="mt-6">
            <button
              type="button"
              onClick={onReviewSchema}
              className="small-caps border border-ink bg-ember px-3 py-1.5 text-xs text-white hover:bg-ember-hover"
            >
              + review schema
            </button>
          </div>
        </>
      ) : null}

      {stage === "build" ? (
        <>
          <p className="mt-4 max-w-lg text-sm text-neutral-600">
            Schema approved. You can chat with your data, or build an overview
            dashboard now.
          </p>
          <div className="mt-6 flex items-center gap-3">
            <button
              type="button"
              onClick={onReviewSchema}
              className="small-caps border border-ink px-3 py-1.5 text-xs hover:bg-neutral-100"
            >
              view schema
            </button>
            <button
              type="button"
              onClick={onBuildDashboard}
              disabled={building}
              className="small-caps border border-ink bg-ember px-3 py-1.5 text-xs text-white hover:bg-ember-hover disabled:cursor-not-allowed disabled:opacity-40"
            >
              {building ? "building\u2026" : "+ build dashboard"}
            </button>
          </div>
        </>
      ) : null}
    </div>
  );
}
