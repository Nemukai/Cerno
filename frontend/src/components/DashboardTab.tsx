import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type MutableRefObject,
} from "react";
import type {
  DashboardPage,
  DiscoveryStatus,
  FileRecord,
  DashboardCell,
  Widget,
} from "../lib/types";
import { widgetFromCell } from "../lib/widgets";
import { WidgetRenderer } from "./WidgetRenderer";

type Props = {
  pages: DashboardPage[];
  cellsByPage: Record<string, DashboardCell[]>;
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

  const totals = buildDashboardTotals(pages, cellsByPage);

  return (
    <div className="px-8 py-6">
      <DashboardHeader
        totals={totals}
        building={building}
        onBuildDashboard={onBuildDashboard}
      />

      <DashboardView pages={pages} cellsByPage={cellsByPage} pageRefs={pageRefs} />
    </div>
  );
}

type DashboardTotals = {
  kpis: number;
  visuals: number;
};

function buildDashboardTotals(
  pages: DashboardPage[],
  cellsByPage: Record<string, DashboardCell[]>,
): DashboardTotals {
  let kpis = 0;
  let visuals = 0;
  for (const page of pages) {
    const pageCells = cellsByPage[page.id] ?? [];
    for (const cell of pageCells) {
      const widget = widgetFromCell(cell);
      if (!isDashboardWidget(widget)) continue;
      if (widget.kind === "kpi") kpis += 1;
      else visuals += 1;
    }
  }
  return { kpis, visuals };
}

function DashboardHeader({
  totals,
  building,
  onBuildDashboard,
}: {
  totals: DashboardTotals;
  building: boolean;
  onBuildDashboard: () => void;
}) {
  return (
    <header className="hairline mb-5 flex flex-wrap items-center justify-between gap-4 border-b pb-4">
      <div>
        <div className="small-caps text-xs text-neutral-500">dashboard</div>
        <h2 className="mt-1 font-mono text-2xl text-ink">Analysis</h2>
      </div>
      <div className="flex flex-wrap items-center gap-4">
        <DashboardFact label="kpis" value={totals.kpis} />
        <DashboardFact label="views" value={totals.visuals} />
        <button
          type="button"
          onClick={onBuildDashboard}
          disabled={building}
          className="small-caps border border-ink px-3 py-1.5 text-xs hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {building ? "building..." : "regenerate"}
        </button>
      </div>
    </header>
  );
}

function DashboardFact({ label, value }: { label: string; value: number }) {
  return (
    <div className="min-w-12 text-right">
      <div className="small-caps text-[11px] text-neutral-500">{label}</div>
      <div className="mt-1 font-mono text-lg text-ink">{value.toLocaleString()}</div>
    </div>
  );
}

function DashboardView({
  pages,
  cellsByPage,
  pageRefs,
}: {
  pages: DashboardPage[];
  cellsByPage: Record<string, DashboardCell[]>;
  pageRefs: MutableRefObject<Record<string, HTMLElement | null>>;
}) {
  const dashboardId = pages[0]?.dashboard_id ?? "dashboard";
  const layoutStorageKey = `cerno:dashboard-layout:v1:${dashboardId}`;
  const editStorageKey = `cerno:dashboard-widget-edits:v1:${dashboardId}`;
  const [layoutsByPage, setLayoutsByPage] = useStoredState<
    Record<string, Record<string, WidgetLayout>>
  >(layoutStorageKey, {});
  const [editsByWidget, setEditsByWidget] = useStoredState<
    Record<string, WidgetEdit>
  >(editStorageKey, {});
  const [selectedWidgetId, setSelectedWidgetId] = useState<string | null>(null);
  const canvasRefs = useRef<Record<string, HTMLDivElement | null>>({});

  const visiblePages = useMemo(
    () =>
      pages
    .map((page) => {
      const cells = cellsByPage[page.id] ?? [];
          const widgets = cells
            .map((cell) => {
              const widget = widgetFromCell(cell);
              if (!isDashboardWidget(widget)) return null;
              return {
                id: cell.id,
                cell,
                widget: applyWidgetEdit(widget, editsByWidget[cell.id]),
              };
            })
            .filter((item): item is DashboardWidget => Boolean(item));
      return { page, widgets };
    })
        .filter(({ widgets }) => widgets.length > 0),
    [cellsByPage, editsByWidget, pages],
  );

  useEffect(() => {
    setLayoutsByPage((current) => {
      let changed = false;
      const next: Record<string, Record<string, WidgetLayout>> = {};

      for (const { page, widgets } of visiblePages) {
        const defaults = buildDefaultLayouts(widgets);
        const currentPage = current[page.id] ?? {};
        const pageLayout: Record<string, WidgetLayout> = {};
        widgets.forEach((widget, index) => {
          pageLayout[widget.id] = clampLayout(
            currentPage[widget.id] ?? defaults[widget.id] ?? defaultLayoutForIndex(widget, index),
            widget.widget.kind,
          );
        });
        next[page.id] = pageLayout;
        if (!samePageLayout(currentPage, pageLayout)) changed = true;
      }

      if (Object.keys(current).length !== Object.keys(next).length) changed = true;
      return changed ? next : current;
    });
  }, [setLayoutsByPage, visiblePages]);

  const selected = visiblePages
    .flatMap(({ page, widgets }) =>
      widgets.map((item) => ({
        ...item,
        page,
        layout: layoutsByPage[page.id]?.[item.id],
      })),
    )
    .find((item) => item.id === selectedWidgetId);

  if (visiblePages.length === 0) {
    return (
      <div className="py-10 text-sm text-neutral-500">
        No dashboard visuals yet. Regenerate the dashboard or ask Cerno for a
        chart from chat.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-[minmax(0,1fr)_320px] gap-5">
      <div className="min-w-0">
      {visiblePages.map(({ page, widgets }) => {
        const num = String(page.position + 1).padStart(2, "0");
        const pageLayout = layoutsByPage[page.id] ?? {};
        const pageHeight = calculatePageHeight(widgets, pageLayout);

        return (
          <section
            key={page.id}
            ref={(el) => {
              pageRefs.current[page.id] = el;
            }}
            className="mb-9"
          >
            <header className="hairline flex items-center justify-between border-b pb-2">
              <h2 className="small-caps text-sm text-ink">
                {num} {page.title}
              </h2>
              <button
                type="button"
                onClick={() =>
                  setLayoutsByPage((current) => ({
                    ...current,
                    [page.id]: buildDefaultLayouts(widgets),
                  }))
                }
                className="small-caps text-[10px] text-neutral-400 hover:text-ember"
              >
                reset layout
              </button>
            </header>
            <div
              ref={(el) => {
                canvasRefs.current[page.id] = el;
              }}
              className="relative mt-4 border border-neutral-200 bg-white"
              style={{
                minHeight: pageHeight,
                backgroundImage:
                  "linear-gradient(#F1F1F1 1px, transparent 1px), linear-gradient(90deg, #F1F1F1 1px, transparent 1px)",
                backgroundSize: `calc(100% / ${GRID_COLUMNS}) ${GRID_ROW_HEIGHT}px`,
              }}
            >
              {widgets.map((item) => {
                const layout = pageLayout[item.id] ?? defaultLayoutForIndex(item, 0);
                const selected = selectedWidgetId === item.id;
                return (
                  <EditableWidgetTile
                    key={item.id}
                    item={item}
                    layout={layout}
                    selected={selected}
                    canvasRef={{ current: canvasRefs.current[page.id] ?? null }}
                    onSelect={() => setSelectedWidgetId(item.id)}
                    onLayoutChange={(nextLayout) =>
                      setLayoutsByPage((current) => ({
                        ...current,
                        [page.id]: {
                          ...(current[page.id] ?? {}),
                          [item.id]: nextLayout,
                        },
                      }))
                    }
                  />
                );
              })}
            </div>
          </section>
        );
      })}
      </div>
      <WidgetInspector
        selected={selected ?? null}
        onPatchLayout={(patch) => {
          if (!selected?.layout) return;
          const layout = selected.layout;
          setLayoutsByPage((current) => ({
            ...current,
            [selected.page.id]: {
              ...(current[selected.page.id] ?? {}),
              [selected.id]: clampLayout(
                { ...layout, ...patch },
                selected.widget.kind,
              ),
            },
          }));
        }}
        onPatchEdit={(patch) => {
          if (!selected) return;
          setEditsByWidget((current) => ({
            ...current,
            [selected.id]: { ...(current[selected.id] ?? {}), ...patch },
          }));
        }}
        onResetEdit={() => {
          if (!selected) return;
          setEditsByWidget((current) => {
            const next = { ...current };
            delete next[selected.id];
            return next;
          });
        }}
      />
    </div>
  );
}

const GRID_COLUMNS = 12;
const GRID_ROW_HEIGHT = 72;

type WidgetLayout = {
  x: number;
  y: number;
  w: number;
  h: number;
};

type WidgetEdit = {
  title?: string;
  hideCaption?: boolean;
  horizontal?: boolean;
};

type DashboardWidget = {
  id: string;
  cell: DashboardCell;
  widget: Widget;
};

type SelectedWidget = DashboardWidget & {
  page: DashboardPage;
  layout?: WidgetLayout;
};

function EditableWidgetTile({
  item,
  layout,
  selected,
  canvasRef,
  onSelect,
  onLayoutChange,
}: {
  item: DashboardWidget;
  layout: WidgetLayout;
  selected: boolean;
  canvasRef: MutableRefObject<HTMLDivElement | null>;
  onSelect: () => void;
  onLayoutChange: (layout: WidgetLayout) => void;
}) {
  const startDrag = (event: React.PointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    onSelect();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const bounds = canvas.getBoundingClientRect();
    const cellWidth = bounds.width / GRID_COLUMNS;
    const startX = event.clientX;
    const startY = event.clientY;
    const startLayout = layout;

    const move = (moveEvent: PointerEvent) => {
      const dx = Math.round((moveEvent.clientX - startX) / cellWidth);
      const dy = Math.round((moveEvent.clientY - startY) / GRID_ROW_HEIGHT);
      onLayoutChange(
        clampLayout(
          {
            ...startLayout,
            x: startLayout.x + dx,
            y: startLayout.y + dy,
          },
          item.widget.kind,
        ),
      );
    };

    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
  };

  const startResize = (event: React.PointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    onSelect();
    const canvas = canvasRef.current;
    if (!canvas) return;
    const bounds = canvas.getBoundingClientRect();
    const cellWidth = bounds.width / GRID_COLUMNS;
    const startX = event.clientX;
    const startY = event.clientY;
    const startLayout = layout;

    const move = (moveEvent: PointerEvent) => {
      const dw = Math.round((moveEvent.clientX - startX) / cellWidth);
      const dh = Math.round((moveEvent.clientY - startY) / GRID_ROW_HEIGHT);
      onLayoutChange(
        clampLayout(
          {
            ...startLayout,
            w: startLayout.w + dw,
            h: startLayout.h + dh,
          },
          item.widget.kind,
        ),
      );
    };

    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };

    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop, { once: true });
  };

  return (
    <article
      role="button"
      tabIndex={0}
      aria-label={`Select widget ${item.widget.title}`}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onSelect();
        }
      }}
      className={`absolute bg-white shadow-[0_1px_0_rgba(14,14,14,0.05)] outline-none transition-[border-color,box-shadow] ${
        selected
          ? "border border-ember ring-2 ring-orange-100"
          : "border border-neutral-200 hover:border-neutral-400"
      }`}
      style={{
        left: `${(layout.x / GRID_COLUMNS) * 100}%`,
        top: layout.y * GRID_ROW_HEIGHT + 4,
        width: `calc(${(layout.w / GRID_COLUMNS) * 100}% - 8px)`,
        height: layout.h * GRID_ROW_HEIGHT - 8,
      }}
    >
      <button
        type="button"
        onPointerDown={startDrag}
        className="flex h-7 w-full cursor-move items-center justify-between border-b border-neutral-100 bg-neutral-50 px-2 text-left"
      >
        <span className="small-caps truncate text-[10px] text-neutral-500">
          {item.widget.kind}
        </span>
        <span className="font-mono text-[10px] text-neutral-300">
          {layout.w}x{layout.h}
        </span>
      </button>
      <div className="h-[calc(100%-28px)] overflow-hidden px-3">
        <WidgetRenderer
          widget={item.widget}
          height={Math.max(120, layout.h * GRID_ROW_HEIGHT - 116)}
        />
      </div>
      <button
        type="button"
        aria-label="Resize widget"
        onPointerDown={startResize}
        className="absolute bottom-0 right-0 h-5 w-5 cursor-se-resize border-l border-t border-orange-200 bg-orange-50 text-[10px] text-ember"
      >
        +
      </button>
    </article>
  );
}

function WidgetInspector({
  selected,
  onPatchLayout,
  onPatchEdit,
  onResetEdit,
}: {
  selected: SelectedWidget | null;
  onPatchLayout: (patch: Partial<WidgetLayout>) => void;
  onPatchEdit: (patch: WidgetEdit) => void;
  onResetEdit: () => void;
}) {
  return (
    <aside className="sticky top-6 h-[calc(100vh-8rem)] border border-neutral-200 bg-white">
      <div className="border-b border-neutral-200 px-4 py-3">
        <div className="small-caps text-xs text-neutral-500">visual</div>
        <div className="mt-1 font-mono text-sm text-ink">
          {selected ? selected.widget.title : "Select a widget"}
        </div>
      </div>
      {selected ? (
        <div className="h-[calc(100%-57px)] overflow-y-auto px-4 py-4">
          <InspectorSection title="Position">
            <Stepper
              label="x"
              value={selected.layout?.x ?? 0}
              onChange={(value) => onPatchLayout({ x: value })}
            />
            <Stepper
              label="y"
              value={selected.layout?.y ?? 0}
              onChange={(value) => onPatchLayout({ y: value })}
            />
            <Stepper
              label="width"
              value={selected.layout?.w ?? 1}
              onChange={(value) => onPatchLayout({ w: value })}
            />
            <Stepper
              label="height"
              value={selected.layout?.h ?? 1}
              onChange={(value) => onPatchLayout({ h: value })}
            />
          </InspectorSection>

          <InspectorSection title="Fields">
            <WidgetFields widget={selected.widget} />
          </InspectorSection>

          <InspectorSection title="Format">
            <label className="block">
              <span className="small-caps text-[10px] text-neutral-500">
                title
              </span>
              <input
                value={selected.widget.title}
                onChange={(event) => onPatchEdit({ title: event.target.value })}
                className="mt-1 w-full border border-neutral-200 px-2 py-1.5 text-xs text-ink focus:outline-none focus:ring-1 focus:ring-ember"
              />
            </label>
            <label className="mt-3 flex items-center justify-between gap-3 text-xs text-ink">
              <span>Hide caption</span>
              <input
                type="checkbox"
                checked={selected.widget.caption === null}
                onChange={(event) =>
                  onPatchEdit({ hideCaption: event.target.checked })
                }
              />
            </label>
            {selected.widget.kind === "bar" ? (
              <label className="mt-3 flex items-center justify-between gap-3 text-xs text-ink">
                <span>Horizontal bars</span>
                <input
                  type="checkbox"
                  checked={Boolean(selected.widget.options.horizontal)}
                  onChange={(event) =>
                    onPatchEdit({ horizontal: event.target.checked })
                  }
                />
              </label>
            ) : null}
            <button
              type="button"
              onClick={onResetEdit}
              className="small-caps mt-4 border border-neutral-300 px-2 py-1 text-[10px] text-neutral-500 hover:border-ink hover:text-ink"
            >
              reset visual edits
            </button>
          </InspectorSection>
        </div>
      ) : (
        <div className="px-4 py-5 text-xs leading-5 text-neutral-500">
          Click a dashboard tile to inspect its axes, values, and formatting.
          Drag the tile header to move it; use the corner handle to resize.
        </div>
      )}
    </aside>
  );
}

function InspectorSection({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <section className="border-b border-neutral-200 py-4 first:pt-0">
      <div className="small-caps mb-3 text-[11px] text-ember">{title}</div>
      <div className="space-y-2">{children}</div>
    </section>
  );
}

function Stepper({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <div className="grid grid-cols-[1fr_auto] items-center gap-2">
      <div className="small-caps text-[10px] text-neutral-500">{label}</div>
      <div className="flex items-center border border-neutral-200">
        <button
          type="button"
          onClick={() => onChange(value - 1)}
          className="h-6 w-6 text-neutral-500 hover:bg-neutral-100"
        >
          -
        </button>
        <div className="w-8 text-center font-mono text-xs text-ink">{value}</div>
        <button
          type="button"
          onClick={() => onChange(value + 1)}
          className="h-6 w-6 text-neutral-500 hover:bg-neutral-100"
        >
          +
        </button>
      </div>
    </div>
  );
}

function WidgetFields({ widget }: { widget: Widget }) {
  const fields = describeWidgetFields(widget);
  return (
    <div className="space-y-2">
      {fields.map((field) => (
        <div
          key={field.label}
          className="grid grid-cols-[76px_minmax(0,1fr)] gap-2 text-xs"
        >
          <div className="small-caps text-[10px] text-neutral-500">
            {field.label}
          </div>
          <div className="min-w-0 font-mono text-[11px] leading-5 text-ink">
            {field.value}
          </div>
        </div>
      ))}
    </div>
  );
}

function describeWidgetFields(widget: Widget): Array<{ label: string; value: string }> {
  const data = widget.data;
  if (widget.kind === "bar" || widget.kind === "line") {
    const categories = getStringArray(data.categories) ?? getStringArray(data.labels);
    const series = getSeriesNames(data.series);
    const values = getNumberArray(data.values);
    const horizontal = Boolean(widget.options.horizontal);
    return [
      { label: horizontal ? "y axis" : "x axis", value: previewList(categories, "categories") },
      {
        label: horizontal ? "x axis" : "y axis",
        value: series.length > 0 ? series.join(", ") : `${values.length} values`,
      },
      { label: "source", value: widget.kind },
    ];
  }
  if (widget.kind === "pie") {
    const items = getItems(data.items);
    return [
      { label: "legend", value: previewList(items.map((item) => item.name), "segments") },
      { label: "values", value: `${items.length} segment values` },
    ];
  }
  if (widget.kind === "table") {
    const columns = getStringArray(data.columns) ?? [];
    const rows = Array.isArray(data.rows) ? data.rows.length : 0;
    return [
      { label: "columns", value: previewList(columns, "columns") },
      { label: "rows", value: rows.toLocaleString() },
    ];
  }
  if (widget.kind === "kpi") {
    return [
      { label: "metric", value: String(data.value ?? "value") },
      { label: "label", value: String(data.label ?? "none") },
    ];
  }
  if (widget.kind === "markdown") {
    const text = typeof data.text === "string" ? data.text : "";
    return [
      { label: "text", value: `${text.length.toLocaleString()} characters` },
    ];
  }
  return [{ label: "kind", value: widget.kind }];
}

function getStringArray(value: unknown): string[] | null {
  if (!Array.isArray(value)) return null;
  return value.map((item) => String(item));
}

function getNumberArray(value: unknown): number[] {
  if (!Array.isArray(value)) return [];
  return value.map(Number).filter(Number.isFinite);
}

function getSeriesNames(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) =>
      item && typeof item === "object" && "name" in item
        ? String((item as { name?: unknown }).name ?? "value")
        : "value",
    )
    .filter(Boolean);
}

function getItems(value: unknown): Array<{ name: string; value: number }> {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => {
      if (!item || typeof item !== "object") return null;
      const row = item as { name?: unknown; value?: unknown };
      return { name: String(row.name ?? "item"), value: Number(row.value ?? 0) };
    })
    .filter((item): item is { name: string; value: number } => Boolean(item));
}

function previewList(items: string[] | null | undefined, fallback: string) {
  if (!items || items.length === 0) return fallback;
  const preview = items.slice(0, 4).join(", ");
  return items.length > 4 ? `${preview} +${items.length - 4}` : preview;
}

function applyWidgetEdit(widget: Widget, edit?: WidgetEdit): Widget {
  if (!edit) return widget;
  return {
    ...widget,
    title: edit.title ?? widget.title,
    caption: edit.hideCaption ? null : widget.caption,
    options: {
      ...widget.options,
      ...(edit.horizontal === undefined ? {} : { horizontal: edit.horizontal }),
    },
  };
}

function useStoredState<T>(
  key: string,
  fallback: T,
): [T, React.Dispatch<React.SetStateAction<T>>] {
  const [stored, setStored] = useState<{ key: string; value: T }>(() => ({
    key,
    value: readStoredValue(key, fallback),
  }));

  useEffect(() => {
    setStored((current) =>
      current.key === key
        ? current
        : { key, value: readStoredValue(key, fallback) },
    );
  }, [fallback, key]);

  const value = stored.key === key ? stored.value : readStoredValue(key, fallback);

  const setValue: React.Dispatch<React.SetStateAction<T>> = (update) => {
    setStored((current) => {
      const currentValue =
        current.key === key ? current.value : readStoredValue(key, fallback);
      const nextValue =
        typeof update === "function"
          ? (update as (previous: T) => T)(currentValue)
          : update;
      return { key, value: nextValue };
    });
  };

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(stored.key, JSON.stringify(stored.value));
  }, [stored]);

  return [value, setValue];
}

function readStoredValue<T>(key: string, fallback: T): T {
  if (typeof window === "undefined") return fallback;
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function buildDefaultLayouts(widgets: DashboardWidget[]) {
  const layouts: Record<string, WidgetLayout> = {};
  let x = 0;
  let y = 0;
  let rowHeight = 0;

  for (const item of widgets) {
    const size = defaultWidgetSize(item.widget.kind);
    if (x + size.w > GRID_COLUMNS) {
      x = 0;
      y += rowHeight;
      rowHeight = 0;
    }
    layouts[item.id] = { x, y, ...size };
    x += size.w;
    rowHeight = Math.max(rowHeight, size.h);
  }

  return layouts;
}

function defaultLayoutForIndex(item: DashboardWidget, index: number) {
  const size = defaultWidgetSize(item.widget.kind);
  return {
    x: (index * size.w) % GRID_COLUMNS,
    y: Math.floor((index * size.w) / GRID_COLUMNS) * size.h,
    ...size,
  };
}

function defaultWidgetSize(kind: Widget["kind"]): Pick<WidgetLayout, "w" | "h"> {
  if (kind === "kpi") return { w: 3, h: 2 };
  if (kind === "table") return { w: 6, h: 5 };
  if (kind === "markdown") return { w: 4, h: 3 };
  return { w: 6, h: 5 };
}

function minWidgetSize(kind: Widget["kind"]): Pick<WidgetLayout, "w" | "h"> {
  if (kind === "kpi") return { w: 2, h: 2 };
  if (kind === "markdown") return { w: 3, h: 2 };
  return { w: 4, h: 3 };
}

function clampLayout(layout: WidgetLayout, kind: Widget["kind"]): WidgetLayout {
  const min = minWidgetSize(kind);
  const w = Math.min(GRID_COLUMNS, Math.max(min.w, layout.w));
  const h = Math.max(min.h, Math.min(10, layout.h));
  return {
    x: Math.min(GRID_COLUMNS - w, Math.max(0, layout.x)),
    y: Math.max(0, layout.y),
    w,
    h,
  };
}

function calculatePageHeight(
  widgets: DashboardWidget[],
  layouts: Record<string, WidgetLayout>,
) {
  const bottom = widgets.reduce((max, item, index) => {
    const layout = layouts[item.id] ?? defaultLayoutForIndex(item, index);
    return Math.max(max, layout.y + layout.h);
  }, 5);
  return bottom * GRID_ROW_HEIGHT + 12;
}

function samePageLayout(
  a: Record<string, WidgetLayout>,
  b: Record<string, WidgetLayout>,
) {
  const aKeys = Object.keys(a);
  const bKeys = Object.keys(b);
  if (aKeys.length !== bKeys.length) return false;
  return bKeys.every((key) => {
    const left = a[key];
    const right = b[key];
    return (
      Boolean(left && right) &&
      left?.x === right?.x &&
      left?.y === right?.y &&
      left?.w === right?.w &&
      left?.h === right?.h
    );
  });
}

function isDashboardWidget(widget: Widget | null): widget is Widget {
  if (!widget) return false;
  const title = widget.title.toLowerCase();
  const text =
    typeof widget.data.text === "string" ? widget.data.text.toLowerCase() : "";
  const dashboardNoise = [
    "relationship",
    "relationships",
    "foreign key",
    "join path",
    "join key",
    "schema",
    "data map",
    "linked table",
    "linked tables",
  ];
  return !(
    title.includes("anomal") ||
    title.includes("flagged row") ||
    title.includes("flagged rows") ||
    dashboardNoise.some((term) => title.includes(term) || text.includes(term))
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
    build: { num: "04", title: "generate the dashboard" },
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
            Schema approved. Cerno will generate the KPI graphics and chart cells
            automatically; you can also start it manually if needed.
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
