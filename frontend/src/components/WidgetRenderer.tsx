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

const baseAxis = {
  axisLine: { lineStyle: { color: HAIRLINE } },
  axisTick: { lineStyle: { color: HAIRLINE } },
  axisLabel: { color: GREY, fontFamily: "IBM Plex Mono", fontSize: 11 },
  splitLine: { lineStyle: { color: HAIRLINE } },
};

function buildBarOption(data: CategoryData): Record<string, unknown> {
  const categories = data.categories ?? data.labels ?? [];
  const series =
    data.series ??
    (data.values ? [{ name: "value", data: data.values }] : []);
  return {
    grid: { left: 48, right: 16, top: 16, bottom: 28 },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "category", data: categories, ...baseAxis },
    yAxis: { type: "value", ...baseAxis },
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

function TableWidget({ data }: { data: TableData }) {
  const cols = data.columns ?? [];
  const rows = data.rows ?? [];
  return (
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
        {rows.map((row, ri) => (
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
  const { kind, title, data, caption } = widget;

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
        <EChart option={buildBarOption(data as CategoryData)} />
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
        <EChart option={buildPieOption(data as PieData)} />
        {footer}
      </div>
    );
  }

  if (kind === "table") {
    return (
      <div className="py-4">
        {header}
        <TableWidget data={data as TableData} />
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
