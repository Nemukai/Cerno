import type { NotebookCell, Widget } from "./types";

type WidgetEnvelope = {
  widget?: unknown;
};

function isWidget(value: unknown): value is Widget {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<Widget>;
  return (
    typeof candidate.kind === "string" &&
    typeof candidate.title === "string" &&
    typeof candidate.data === "object" &&
    candidate.data !== null
  );
}

export function widgetFromCell(cell: NotebookCell): Widget | null {
  if (cell.kind !== "widget") return null;
  const output = cell.output;
  if (!output) return null;
  if (isWidget(output)) return output;
  const envelope = output as WidgetEnvelope;
  return isWidget(envelope.widget) ? envelope.widget : null;
}
