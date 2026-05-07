import { useMemo, useState } from "react";
import type { KpiData, Widget } from "../lib/types";
import { EMBER, GREY, HAIRLINE, INK } from "../lib/echarts";
import { EChart } from "./EChart";
import { KpiCard } from "./KpiCard";

type Props = {
  widget: Widget;
  height?: number;
};

type CategoryData = {
  columns?: string[];
  rows?: TableRow[];
  categories?: string[];
  series?: Array<{ name?: string; data: number[] }>;
  values?: number[];
  labels?: string[];
  items?: Array<{ name: string; value: number }>;
};

type PieData = {
  columns?: string[];
  rows?: TableRow[];
  items?: Array<{ name: string; value: number }>;
};

type TableCell = string | number | boolean | null;
type TableRow = TableCell[];

type TableData = {
  columns?: string[];
  rows?: TableRow[];
};

type MarkdownData = {
  text?: string;
};

type WidgetOptions = {
  horizontal?: boolean;
  interactive?: boolean;
  searchable?: boolean;
  x?: string;
  y?: string;
  category?: string;
  value?: string;
  label?: string;
};

const baseAxis = {
  axisLine: { lineStyle: { color: HAIRLINE } },
  axisTick: { lineStyle: { color: HAIRLINE } },
  axisLabel: { color: GREY, fontFamily: "IBM Plex Mono", fontSize: 11 },
  splitLine: { lineStyle: { color: HAIRLINE } },
};

function formatAxisValue(value: number | string): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return String(value);
  return new Intl.NumberFormat(undefined, {
    maximumFractionDigits: Math.abs(numeric) < 10 ? 2 : 0,
  }).format(numeric);
}

const valueAxis = {
  type: "value",
  ...baseAxis,
  axisLabel: {
    ...baseAxis.axisLabel,
    formatter: formatAxisValue,
  },
};

function chartItems(data: CategoryData): Array<{ name: string; value: number }> {
  if (data.items) return data.items;
  const categories = data.categories ?? data.labels ?? [];
  const values = data.values ?? [];
  return categories.map((name, idx) => ({ name, value: Number(values[idx] ?? 0) }));
}

function numericValue(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const normalized = value.replace(/,/g, "").trim();
  if (!normalized) return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function columnIndex(columns: string[], names: Array<string | undefined>): number {
  for (const name of names) {
    if (!name) continue;
    const index = columns.findIndex(
      (column) => column.toLowerCase() === name.toLowerCase(),
    );
    if (index >= 0) return index;
  }
  return -1;
}

function inferCategoryIndex(columns: string[], rows: TableRow[], options: WidgetOptions) {
  const configured = columnIndex(columns, [options.x, options.category, options.label]);
  if (configured >= 0) return configured;
  const firstText = columns.findIndex((_, index) =>
    rows.some((row) => numericValue(row[index]) === null),
  );
  return firstText >= 0 ? firstText : 0;
}

function inferValueIndex(
  columns: string[],
  rows: TableRow[],
  categoryIndex: number,
  options: WidgetOptions,
) {
  const configured = columnIndex(columns, [options.y, options.value]);
  if (configured >= 0) return configured;
  const firstNumeric = columns.findIndex(
    (_, index) =>
      index !== categoryIndex &&
      rows.some((row) => numericValue(row[index]) !== null),
  );
  return firstNumeric >= 0 ? firstNumeric : categoryIndex === 0 ? 1 : 0;
}

function categoryDataFromTable(
  data: CategoryData,
  options: WidgetOptions,
): CategoryData {
  if (!data.columns || !data.rows) return data;
  const categoryIndex = inferCategoryIndex(data.columns, data.rows, options);
  const valueIndex = inferValueIndex(data.columns, data.rows, categoryIndex, options);
  const items = data.rows
    .map((row) => {
      const value = numericValue(row[valueIndex]);
      if (value === null) return null;
      return {
        name: String(row[categoryIndex] ?? ""),
        value,
      };
    })
    .filter((item): item is { name: string; value: number } => item !== null);
  return {
    categories: items.map((item) => item.name),
    values: items.map((item) => item.value),
  };
}

function pieDataFromTable(data: PieData & CategoryData, options: WidgetOptions): PieData {
  if (!data.columns || !data.rows) return data;
  return { items: chartItems(categoryDataFromTable(data, options)) };
}

function limitItems(
  items: Array<{ name: string; value: number }>,
  limit: number,
): Array<{ name: string; value: number }> {
  return limit === 0 ? items : items.slice(0, limit);
}

function buildBarOption(data: CategoryData, options: WidgetOptions = {}): Record<string, unknown> {
  const categories = data.categories ?? data.labels ?? [];
  const series =
    data.series ??
    (data.values ? [{ name: "value", data: data.values }] : []);
  const horizontal = options.horizontal === true;
  return {
    grid: horizontal
      ? { left: 12, right: 18, top: 16, bottom: 28, containLabel: true }
      : { left: 12, right: 16, top: 16, bottom: 28, containLabel: true },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: horizontal
      ? valueAxis
      : { type: "category", data: categories, ...baseAxis },
    yAxis: horizontal
      ? { type: "category", data: categories, ...baseAxis }
      : valueAxis,
    series: series.map((s) => ({
      name: s.name ?? "value",
      type: "bar",
      data: s.data,
      itemStyle: { color: EMBER, borderRadius: 0 },
      barMaxWidth: 28,
    })),
  };
}

function buildLineOption(data: CategoryData): Record<string, unknown> {
  const categories = data.categories ?? data.labels ?? [];
  const series =
    data.series ??
    (data.values ? [{ name: "value", data: data.values }] : []);
  return {
    grid: { left: 12, right: 16, top: 16, bottom: 28, containLabel: true },
    tooltip: { trigger: "axis" },
    xAxis: { type: "category", data: categories, ...baseAxis },
    yAxis: valueAxis,
    series: series.map((s) => ({
      name: s.name ?? "value",
      type: "line",
      data: s.data,
      smooth: false,
      symbol: "none",
      lineStyle: { color: EMBER, width: 2 },
      itemStyle: { color: EMBER },
    })),
  };
}

function buildPieOption(data: PieData): Record<string, unknown> {
  return {
    tooltip: { trigger: "item" },
    legend: { bottom: 0, textStyle: { color: INK, fontSize: 11 } },
    series: [
      {
        type: "pie",
        radius: ["40%", "70%"],
        data: data.items ?? [],
        itemStyle: { borderColor: "#FFFFFF", borderWidth: 1 },
        label: { color: INK, fontSize: 11 },
        color: [EMBER, "#0E0E0E", "#737373", "#A3A3A3", "#D4D4D4"],
      },
    ],
  };
}

function ChartFilter({
  count,
  limit,
  onChange,
}: {
  count: number;
  limit: number;
  onChange: (limit: number) => void;
}) {
  if (count <= 5) return null;
  const options = [
    { label: "top 5", value: 5 },
    { label: "top 10", value: 10 },
    { label: "all", value: 0 },
  ];
  return (
    <div className="flex items-center gap-1">
      {options.map((item) => (
        <button
          key={item.label}
          type="button"
          onClick={() => onChange(item.value)}
          className={`small-caps border px-1.5 py-0.5 text-[10px] ${
            limit === item.value
              ? "border-ink text-ink"
              : "border-neutral-200 text-neutral-400 hover:text-ink"
          }`}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}

function FilterableBar({
  data,
  options,
  height,
}: {
  data: CategoryData;
  options: WidgetOptions;
  height?: number;
}) {
  const [limit, setLimit] = useState(10);
  const normalizedData = useMemo(
    () => categoryDataFromTable(data, options),
    [data, options],
  );
  const items = useMemo(() => chartItems(normalizedData), [normalizedData]);
  const visible = useMemo(() => limitItems(items, limit), [items, limit]);
  const chartData = {
    categories: visible.map((item) => item.name),
    values: visible.map((item) => item.value),
  };
  return (
    <>
      {options.interactive ? (
        <div className="mb-2 flex justify-end">
          <ChartFilter count={items.length} limit={limit} onChange={setLimit} />
        </div>
      ) : null}
      <EChart
        height={height ?? (options.horizontal ? 300 : 240)}
        option={buildBarOption(chartData, options)}
      />
    </>
  );
}

function FilterablePie({
  data,
  height,
}: {
  data: PieData & CategoryData;
  height?: number;
}) {
  const [limit, setLimit] = useState(8);
  const normalizedData = useMemo(() => pieDataFromTable(data, {}), [data]);
  const items = useMemo(() => chartItems(normalizedData as CategoryData), [normalizedData]);
  const visible = useMemo(() => limitItems(items, limit), [items, limit]);
  return (
    <>
      <div className="mb-2 flex justify-end">
        <ChartFilter count={items.length} limit={limit} onChange={setLimit} />
      </div>
      <EChart height={height} option={buildPieOption({ items: visible })} />
    </>
  );
}

function TableWidget({ data, searchable }: { data: TableData; searchable?: boolean }) {
  const [query, setQuery] = useState("");
  const cols = data.columns ?? [];
  const rows = data.rows ?? [];
  const visibleRows = query
    ? rows.filter((row) =>
        row.some((cell) => String(cell).toLowerCase().includes(query.toLowerCase())),
      )
    : rows;
  return (
    <>
      {searchable && rows.length > 6 ? (
        <input
          type="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Filter rows"
          className="mb-3 w-full border border-neutral-200 bg-white px-2 py-1.5 text-xs focus:outline-none focus:ring-1 focus:ring-ink"
        />
      ) : null}
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr>
            {cols.map((c) => (
              <th
                key={c}
                className="small-caps hairline border-b px-2 py-2 text-left text-xs text-neutral-500"
              >
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visibleRows.map((row, ri) => (
            <tr key={ri} className="hairline border-b">
              {row.map((cell, ci) => (
                <td
                  key={ci}
                  className={`px-2 py-1.5 ${
                    typeof cell === "number" ? "font-mono tabular-nums" : ""
                  }`}
                >
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

function MarkdownWidget({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);
  const normalized = text.trim();
  const preview = summarizeText(normalized, 220);
  const shouldCollapse = normalized.length > preview.length;
  const visibleText = expanded || !shouldCollapse ? normalized : preview;
  const paras = visibleText.split(/\n{2,}/).filter(Boolean);

  return (
    <div className="space-y-2 text-sm leading-relaxed text-ink">
      {paras.map((p, i) => (
        <p key={i}>{p}</p>
      ))}
      {shouldCollapse ? (
        <button
          type="button"
          onClick={() => setExpanded((current) => !current)}
          className="small-caps text-[10px] text-ember hover:text-ember-hover"
        >
          {expanded ? "show less" : "show note"}
        </button>
      ) : null}
    </div>
  );
}

function summarizeText(text: string, maxLength: number) {
  const singleLine = text.replace(/\s+/g, " ").trim();
  if (singleLine.length <= maxLength) return singleLine;
  const sentenceEnd = singleLine.slice(0, maxLength).lastIndexOf(".");
  const cut = sentenceEnd > 80 ? sentenceEnd + 1 : maxLength;
  return `${singleLine.slice(0, cut).trim()}...`;
}

function Caption({ caption }: { caption: string }) {
  const [expanded, setExpanded] = useState(false);
  const preview = summarizeText(caption, 110);
  const shouldCollapse = caption.trim().length > preview.length;

  return (
    <div className="mt-2 text-xs leading-5 text-neutral-500">
      {expanded || !shouldCollapse ? caption : preview}
      {shouldCollapse ? (
        <button
          type="button"
          onClick={() => setExpanded((current) => !current)}
          className="small-caps ml-2 text-[10px] text-ember hover:text-ember-hover"
        >
          {expanded ? "less" : "more"}
        </button>
      ) : null}
    </div>
  );
}

export function WidgetRenderer({ widget, height }: Props) {
  const { kind, title, data, caption, options } = widget;
  const widgetOptions = options as WidgetOptions;

  if (kind === "kpi") {
    return <KpiCard title={title} data={data as KpiData} caption={caption} />;
  }

  const header = (
    <div className="mb-2 flex items-baseline justify-between">
      <h3 className="small-caps text-xs text-neutral-500">{title}</h3>
    </div>
  );

  const footer = caption ? <Caption caption={caption} /> : null;

  if (kind === "bar") {
    return (
      <div className="py-4">
        {header}
        <FilterableBar
          data={data as CategoryData}
          options={widgetOptions}
          height={height}
        />
        {footer}
      </div>
    );
  }

  if (kind === "line") {
    return (
      <div className="py-4">
        {header}
        <EChart
          height={height}
          option={buildLineOption(categoryDataFromTable(data as CategoryData, widgetOptions))}
        />
        {footer}
      </div>
    );
  }

  if (kind === "pie") {
    return (
      <div className="py-4">
        {header}
        <FilterablePie
          data={pieDataFromTable(data as PieData & CategoryData, widgetOptions) as PieData & CategoryData}
          height={height}
        />
        {footer}
      </div>
    );
  }

  if (kind === "table") {
    return (
      <div className="py-4">
        {header}
        <TableWidget data={data as TableData} searchable={widgetOptions.searchable} />
        {footer}
      </div>
    );
  }

  if (kind === "markdown") {
    return (
      <div className="py-4">
        {header}
        <MarkdownWidget text={(data as MarkdownData).text ?? ""} />
        {footer}
      </div>
    );
  }

  return (
    <div className="py-4">
      {header}
      <div className="text-xs text-neutral-500">unknown widget kind: {kind}</div>
    </div>
  );
}
