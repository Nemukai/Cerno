import { useEffect, useRef } from "react";
import type { DashboardPage, NotebookCell, Widget } from "../lib/types";
import { WidgetRenderer } from "./WidgetRenderer";

type Props = {
  pages: DashboardPage[];
  cellsByPage: Record<string, NotebookCell[]>;
  focusPageId: string | null;
  onBuildDashboard: () => void;
  building: boolean;
  hasFiles: boolean;
};

function widgetFromCell(cell: NotebookCell): Widget | null {
  if (cell.kind !== "widget") return null;
  const out = cell.output;
  if (!out) return null;
  if (
    typeof out.kind === "string" &&
    typeof out.title === "string" &&
    typeof out.data === "object"
  ) {
    return out as unknown as Widget;
  }
  return null;
}

export function DashboardTab({
  pages,
  cellsByPage,
  focusPageId,
  onBuildDashboard,
  building,
  hasFiles,
}: Props) {
  const pageRefs = useRef<Record<string, HTMLElement | null>>({});

  useEffect(() => {
    if (!focusPageId) return;
    const node = pageRefs.current[focusPageId];
    if (node) node.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [focusPageId]);

  if (pages.length === 0) {
    return (
      <div className="px-8 py-10">
        <div className="small-caps text-xs text-neutral-500">dashboard</div>
        <p className="mt-4 max-w-lg text-sm text-neutral-600">
          No dashboard yet. Build the overview from your uploaded files and
          confirmed links.
        </p>
        <button
          type="button"
          onClick={onBuildDashboard}
          disabled={!hasFiles || building}
          className="small-caps mt-4 border border-ink px-3 py-1.5 text-xs hover:bg-neutral-100 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {building ? "building\u2026" : "+ build dashboard"}
        </button>
      </div>
    );
  }

  return (
    <div className="px-8 py-6">
      {pages.map((page) => {
        const cells = cellsByPage[page.id] ?? [];
        const widgets = cells
          .map(widgetFromCell)
          .filter((w): w is Widget => w !== null);
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
              <div className="grid grid-cols-1 gap-x-10 md:grid-cols-2">
                {widgets.map((w, i) => (
                  <div key={i} className="hairline border-b">
                    <WidgetRenderer widget={w} />
                  </div>
                ))}
              </div>
            )}
          </section>
        );
      })}
    </div>
  );
}
