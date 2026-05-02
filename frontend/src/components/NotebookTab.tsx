import type { DashboardPage, NotebookCell } from "../lib/types";
import { widgetFromCell } from "../lib/widgets";
import { WidgetRenderer } from "./WidgetRenderer";

type Props = {
  pages: DashboardPage[];
  cellsByPage: Record<string, NotebookCell[]>;
  focusPageId: string | null;
};

function formatRunStatus(cell: NotebookCell): string {
  if (!cell.last_run_status && !cell.last_run_at) return "";
  const parts: string[] = [];
  if (cell.last_run_status) parts.push(cell.last_run_status);
  if (cell.last_run_at) parts.push(new Date(cell.last_run_at).toLocaleString());
  return parts.join(" \u00b7 ");
}

export function NotebookTab({ pages, cellsByPage, focusPageId }: Props) {
  const activePage =
    (focusPageId ? pages.find((p) => p.id === focusPageId) : null) ??
    pages[0] ??
    null;

  if (!activePage) {
    return (
      <div className="px-8 py-10 text-sm text-neutral-500">
        No notebook yet. Build a dashboard or ask a question in chat.
      </div>
    );
  }

  const cells = cellsByPage[activePage.id] ?? [];
  const num = String(activePage.position + 1).padStart(2, "0");

  return (
    <div className="px-8 py-6">
      <header className="hairline mb-6 border-b pb-2">
        <h2 className="small-caps text-sm text-ink">
          {num} {activePage.title}
        </h2>
      </header>
      <div className="space-y-6">
        {cells.map((cell) => (
          <div key={cell.id}>
            <div className="small-caps mb-1 flex items-center justify-between text-[10px] text-neutral-500">
              <span>
                {`cell ${String(cell.order_index + 1).padStart(2, "0")} \u00b7 ${cell.kind}`}
              </span>
              <span>{formatRunStatus(cell)}</span>
            </div>
            {cell.kind === "widget" ? (
              <WidgetCellOutput cell={cell} />
            ) : (
              <pre className="hairline overflow-x-auto border-l-2 border-l-ink bg-white p-3 font-mono text-xs text-ink">
                {cell.code}
              </pre>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function WidgetCellOutput({ cell }: { cell: NotebookCell }) {
  const widget = widgetFromCell(cell);
  if (!widget) {
    return (
      <div className="text-xs text-neutral-500">(no widget output)</div>
    );
  }
  return <WidgetRenderer widget={widget} />;
}
