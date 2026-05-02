import { useMemo, useState } from "react";
import type { KpiData, Widget } from "../lib/types";
import { EMBER, GREY, HAIRLINE, INK } from "../lib/echarts";
import { EChart } from "./EChart";
import { KpiCard } from "./KpiCard";

type Props = {
  widget: Widget;
};

type CategoryData = {
  categories?: string[];
  series?: Array<{ name?: string; data: number[] }>;
  values?: number[];
  labels?: string[];
  items?: Array<{ name: string; value: number }>;
};

type PieData = {
  items?: Array<{ name: string; value: number }>;
};

type TableData = {
  columns?: string[];
  rows?: Array<Array<string | number>>;
};

type MarkdownData = {
  text?: string;
};

type WidgetOptions = {
  horizontal?: boolean;
  interactive?: boolean;
  searchable?: boolean;
};

const baseAxis = {
  axisLine: { lineStyle: { color: HAIRLINE } },
  axisTick: { lineStyle: { color: HAIRLINE } },
  axisLabel: { color: GREY, fontFamily: "IBM Plex Mono", fontSize: 11 },
  splitLine: { lineStyle: { color: HAIRLINE } },
};

function chartItems(data: CategoryData): Array<{ name: string; value: number }> {
  if (data.items) return data.items;
  const categories = data.categories ?? data.labels ?? [];
  const values = data.values ?? [];
  return categories.map((name, idx) => ({ name, value: Number(values[idx] ?? 0) }));
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
      ? { left: 112, right: 18, top: 16, bottom: 28 }
      : { left: 48, right: 16, top: 16, bottom: 28 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: horizontal
      ? { type: "value", ...baseAxis }
      : { type: "category", data: categories, ...baseAxis },
    yAxis: horizontal
      ? { type: "category", data: categories, ...baseAxis }
      : { type: "value", ...baseAxis },
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
    grid: { left: 48, right: 16, top: 16, bottom: 28 },
    tooltip: { trigger: "axis" },
    xAxis: { type: "category", data: categories, ...baseAxis },
    yAxis: { type: "value", ...baseAxis },
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

function FilterableBar({ data, options }: { data: CategoryData; options: WidgetOptions }) {
  const [limit, setLimit] = useState(10);
  const items = useMemo(() => chartItems(data), [data]);
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
        height={options.horizontal ? 300 : 240}
        option={buildBarOption(chartData, options)}
      />
    </>
  );
}

function FilterablePie({ data }: { data: PieData & CategoryData }) {
  const [limit, setLimit] = useState(8);
  const items = useMemo(() => chartItems(data), [data]);
  const visible = useMemo(() => limitItems(items, limit), [items, limit]);
  return (
    <>
      <div className="mb-2 flex justify-end">
        <ChartFilter count={items.length} limit={limit} onChange={setLimit} />
      </div>
      <EChart option={buildPieOption({ items: visible })} />
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
  const paras = text.split(/\n{2,}/);
  return (
    <div className="space-y-2 text-sm leading-relaxed text-ink">
      {paras.map((p, i) => (
        <p key={i}>{p}</p>
      ))}
    </div>
  );
}

export function WidgetRenderer({ widget }: Props) {
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

  const footer = caption ? (
    <div className="mt-2 text-xs text-neutral-500">{caption}</div>
  ) : null;

  if (kind === "bar") {
    return (
      <div className="py-4">
        {header}
        <FilterableBar data={data as CategoryData} options={widgetOptions} />
        {footer}
      </div>
    );
  }

  if (kind === "line") {
    return (
      <div className="py-4">
        {header}
        <EChart option={buildLineOption(data as CategoryData)} />
        {footer}
      </div>
    );
  }

  if (kind === "pie") {
    return (
      <div className="py-4">
        {header}
        <FilterablePie data={data as PieData & CategoryData} />
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
