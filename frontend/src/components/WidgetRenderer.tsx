import { useMemo, useState } from "react";
import type { KpiData, Widget } from "../lib/types";
import { EMBER, GREY, HAIRLINE, INK } from "../lib/echarts";
import { EChart } from "./EChart";
import { KpiCard } from "./KpiCard";

type Props = {
  widget: Widget;
  height?: number;
};

type TableCell = string | number | boolean | null;
type TableRow = TableCell[];

type SeriesDatum = {
  name?: string;
  data: number[];
};

type CategoryData = {
  columns?: string[];
  rows?: TableRow[];
  categories?: string[];
  series?: SeriesDatum[];
  values?: number[];
  labels?: string[];
  items?: Array<{ name: string; value: number }>;
};

type PieData = {
  columns?: string[];
  rows?: TableRow[];
  items?: Array<{ name: string; value: number }>;
};

type TableData = {
  columns?: string[];
  rows?: TableRow[];
};

type MarkdownData = {
  text?: string;
};

type WidgetOptions = {
  bins?: number;
  category?: string;
  chartType?: string;
  group?: string;
  horizontal?: boolean;
  interactive?: boolean;
  label?: string;
  layout?: string;
  orientation?: string;
  searchable?: boolean;
  series?: string;
  size?: string;
  source?: string;
  stacked?: boolean;
  target?: string;
  time?: string;
  value?: string;
  variant?: string;
  x?: string;
  y?: string;
};

const PALETTE = [EMBER, "#0E0E0E", "#4B5563", "#9A6A3A", "#737373", "#B45309", "#A3A3A3"];
const CHART_KIND_HINTS = [
  ["stacked area", "stacked_area"],
  ["area chart", "area"],
  ["histogram", "histogram"],
  ["scatterplot", "scatter"],
  ["scatter plot", "scatter"],
  ["heatmap", "heatmap"],
  ["heat map", "heatmap"],
  ["boxplot", "boxplot"],
  ["box plot", "boxplot"],
  ["waterfall", "waterfall"],
  ["sankey", "sankey"],
  ["timeline", "timeline"],
  ["horizontal bar", "horizontal_bar"],
  ["grouped bar", "grouped_bar"],
  ["stacked bar", "stacked_bar"],
  ["stacked chart", "stacked_bar"],
] as const;

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

function numericValue(value: unknown): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const normalized = value.replace(/,/g, "").trim();
  if (!normalized) return null;
  const parsed = Number(normalized);
  return Number.isFinite(parsed) ? parsed : null;
}

function cellText(value: unknown): string {
  if (value === null || value === undefined) return "";
  return String(value);
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

function inferSeriesIndex(
  columns: string[],
  rows: TableRow[],
  categoryIndex: number,
  valueIndex: number,
  options: WidgetOptions,
) {
  const configured = columnIndex(columns, [options.series, options.group]);
  if (configured >= 0) return configured;
  return columns.findIndex(
    (_, index) =>
      index !== categoryIndex &&
      index !== valueIndex &&
      rows.some((row) => cellText(row[index]) !== "" && numericValue(row[index]) === null),
  );
}

function chartItems(data: CategoryData): Array<{ name: string; value: number }> {
  if (data.items) return data.items;
  const categories = data.categories ?? data.labels ?? [];
  const values = data.values ?? [];
  return categories.map((name, idx) => ({ name, value: Number(values[idx] ?? 0) }));
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
        name: cellText(row[categoryIndex]),
        value,
      };
    })
    .filter((item): item is { name: string; value: number } => item !== null);
  return {
    categories: items.map((item) => item.name),
    values: items.map((item) => item.value),
  };
}

function seriesDataFromTable(data: CategoryData, options: WidgetOptions): CategoryData {
  if (data.series || !data.columns || !data.rows) return data;
  const categoryIndex = inferCategoryIndex(data.columns, data.rows, options);
  const valueIndex = inferValueIndex(data.columns, data.rows, categoryIndex, options);
  const seriesIndex = inferSeriesIndex(data.columns, data.rows, categoryIndex, valueIndex, options);

  if (seriesIndex >= 0 && seriesIndex !== categoryIndex && seriesIndex !== valueIndex) {
    const categories = Array.from(new Set(data.rows.map((row) => cellText(row[categoryIndex]))));
    const groups = Array.from(new Set(data.rows.map((row) => cellText(row[seriesIndex]))));
    const series = groups.map((group) => ({
      name: group,
      data: categories.map((category) => {
        const row = data.rows?.find(
          (candidate) =>
            cellText(candidate[categoryIndex]) === category &&
            cellText(candidate[seriesIndex]) === group,
        );
        return numericValue(row?.[valueIndex]) ?? 0;
      }),
    }));
    return { categories, series };
  }

  const numericColumns = data.columns
    .map((column, index) => ({ column, index }))
    .filter(
      ({ index }) =>
        index !== categoryIndex &&
        data.rows?.some((row) => numericValue(row[index]) !== null),
    );
  if (numericColumns.length <= 1) return categoryDataFromTable(data, options);

  return {
    categories: data.rows.map((row) => cellText(row[categoryIndex])),
    series: numericColumns.map(({ column, index }) => ({
      name: column,
      data: data.rows?.map((row) => numericValue(row[index]) ?? 0) ?? [],
    })),
  };
}

type BarVariant = "bar" | "horizontal_bar" | "grouped_bar" | "stacked_bar";

function optionText(value: unknown): string {
  return typeof value === "string" ? value.toLowerCase() : "";
}

function hasMultipleNumericSeries(data: CategoryData, options: WidgetOptions): boolean {
  if (data.series && data.series.length > 1) return true;
  if (!data.columns || !data.rows) return false;
  const categoryIndex = inferCategoryIndex(data.columns, data.rows, options);
  const valueIndex = inferValueIndex(data.columns, data.rows, categoryIndex, options);
  const seriesIndex = inferSeriesIndex(data.columns, data.rows, categoryIndex, valueIndex, options);
  if (seriesIndex >= 0 && seriesIndex !== categoryIndex) return true;
  return (
    data.columns.filter(
      (_, index) =>
        index !== categoryIndex &&
        data.rows?.some((row) => numericValue(row[index]) !== null),
    ).length > 1
  );
}

function resolveBarVariant(kind: BarVariant, data: CategoryData, options: WidgetOptions): BarVariant {
  if (kind !== "bar") return kind;
  const requested = [
    optionText(options.chartType),
    optionText(options.variant),
    optionText(options.layout),
    optionText(options.orientation),
  ];
  if (requested.some((value) => value === "horizontal" || value === "horizontal_bar")) {
    return "horizontal_bar";
  }
  if (requested.some((value) => value === "stacked" || value === "stacked_bar") || options.stacked === true) {
    return "stacked_bar";
  }
  if (requested.some((value) => value === "grouped" || value === "grouped_bar") || hasMultipleNumericSeries(data, options)) {
    return "grouped_bar";
  }
  return "bar";
}

function textIncludes(text: string, terms: string[]) {
  return terms.some((term) => text.includes(term));
}

function resolveWidgetKind(widget: Widget): Widget["kind"] {
  const text = `${widget.title} ${widget.caption ?? ""}`.toLowerCase();
  const options = (widget.options ?? {}) as WidgetOptions;

  for (const [hint, resolved] of CHART_KIND_HINTS) {
    if (text.includes(hint)) return resolved;
  }

  const data = widget.data as CategoryData;
  const columns = data.columns?.map((column) => column.toLowerCase()) ?? [];

  if (widget.kind === "bar") {
    const valueColumn = columns.find((column) => /pct|percent|share|proportion/.test(column));
    if (valueColumn || textIncludes(text, ["proportion", "share"])) return "stacked_bar";
    return resolveBarVariant("bar", data, options);
  }

  if (widget.kind === "line" && text.includes("area")) return "area";

  if (widget.kind === "table") {
    if (columns.includes("source") && columns.includes("target")) return "sankey";
    if (columns.some((column) => column.includes("time")) && columns.some((column) => column.includes("event"))) {
      return "timeline";
    }
  }

  return widget.kind;
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

function buildBarOption(
  data: CategoryData,
  options: WidgetOptions = {},
  variant: "bar" | "horizontal_bar" | "grouped_bar" | "stacked_bar" = "bar",
): Record<string, unknown> {
  const normalized = seriesDataFromTable(data, options);
  const categories = normalized.categories ?? normalized.labels ?? [];
  const series =
    normalized.series ??
    (normalized.values ? [{ name: "value", data: normalized.values }] : []);
  const horizontal =
    variant === "horizontal_bar" ||
    options.horizontal === true ||
    optionText(options.orientation) === "horizontal" ||
    optionText(options.layout) === "horizontal";
  return {
    color: PALETTE,
    legend: series.length > 1 ? { top: 0, textStyle: { color: INK, fontSize: 11 } } : undefined,
    grid: { left: 12, right: 18, top: series.length > 1 ? 34 : 16, bottom: 28, containLabel: true },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: horizontal ? valueAxis : { type: "category", data: categories, ...baseAxis },
    yAxis: horizontal ? { type: "category", data: categories, ...baseAxis } : valueAxis,
    series: series.map((s) => ({
      name: s.name ?? "value",
      type: "bar",
      stack: variant === "stacked_bar" ? "total" : undefined,
      data: s.data,
      itemStyle: { borderRadius: 0 },
      barMaxWidth: 28,
    })),
  };
}

function buildLineOption(
  data: CategoryData,
  options: WidgetOptions,
  variant: "line" | "area" | "stacked_area" = "line",
): Record<string, unknown> {
  const normalized = seriesDataFromTable(data, options);
  const categories = normalized.categories ?? normalized.labels ?? [];
  const series =
    normalized.series ??
    (normalized.values ? [{ name: "value", data: normalized.values }] : []);
  return {
    color: PALETTE,
    legend: series.length > 1 ? { top: 0, textStyle: { color: INK, fontSize: 11 } } : undefined,
    grid: { left: 12, right: 16, top: series.length > 1 ? 34 : 16, bottom: 28, containLabel: true },
    tooltip: { trigger: "axis" },
    xAxis: { type: "category", data: categories, ...baseAxis },
    yAxis: valueAxis,
    series: series.map((s) => ({
      name: s.name ?? "value",
      type: "line",
      data: s.data,
      smooth: false,
      stack: variant === "stacked_area" ? "total" : undefined,
      symbol: "none",
      areaStyle: variant === "area" || variant === "stacked_area" ? {} : undefined,
      lineStyle: { width: 2 },
    })),
  };
}

function buildPieOption(data: PieData): Record<string, unknown> {
  return {
    color: PALETTE,
    tooltip: { trigger: "item" },
    legend: { bottom: 0, textStyle: { color: INK, fontSize: 11 } },
    series: [
      {
        type: "pie",
        radius: ["40%", "70%"],
        data: data.items ?? [],
        itemStyle: { borderColor: "#FFFFFF", borderWidth: 1 },
        label: { color: INK, fontSize: 11 },
      },
    ],
  };
}

function histogramData(data: CategoryData, options: WidgetOptions): CategoryData {
  if (data.categories || data.values || data.items) return categoryDataFromTable(data, options);
  if (data.columns && data.rows) {
    const categoryIndex = inferCategoryIndex(data.columns, data.rows, options);
    const valueIndex = inferValueIndex(data.columns, data.rows, categoryIndex, options);
    const categoryColumn = data.columns[categoryIndex]?.toLowerCase() ?? "";
    const valueColumn = data.columns[valueIndex]?.toLowerCase() ?? "";
    if (
      textIncludes(categoryColumn, ["bin", "bucket", "range"]) ||
      textIncludes(valueColumn, ["count", "frequency", "freq"])
    ) {
      return categoryDataFromTable(data, options);
    }
  }
  const values =
    data.values ??
    (data.columns && data.rows
      ? data.rows
          .map((row) => numericValue(row[inferValueIndex(data.columns ?? [], data.rows ?? [], -1, options)]))
          .filter((value): value is number => value !== null)
      : []);
  if (values.length === 0) return { categories: [], values: [] };
  const binCount = Math.max(2, Math.min(40, Number(options.bins ?? 12)));
  const min = Math.min(...values);
  const max = Math.max(...values);
  const width = max === min ? 1 : (max - min) / binCount;
  const bins = Array.from({ length: binCount }, (_, index) => ({
    start: min + width * index,
    end: min + width * (index + 1),
    count: 0,
  }));
  values.forEach((value) => {
    const index = Math.min(binCount - 1, Math.floor((value - min) / width));
    const bin = bins[index];
    if (bin) bin.count += 1;
  });
  return {
    categories: bins.map((bin) => `${formatAxisValue(bin.start)}-${formatAxisValue(bin.end)}`),
    values: bins.map((bin) => bin.count),
  };
}

function buildScatterOption(data: CategoryData, options: WidgetOptions): Record<string, unknown> {
  const points = scatterPoints(data, options);
  return {
    color: PALETTE,
    grid: { left: 12, right: 16, top: 16, bottom: 28, containLabel: true },
    tooltip: {
      trigger: "item",
      formatter: (params: { data?: unknown[] }) => {
        const point = params.data ?? [];
        return `${point[3] ?? ""}<br/>x: ${point[0]}<br/>y: ${point[1]}`;
      },
    },
    xAxis: valueAxis,
    yAxis: valueAxis,
    series: [
      {
        type: "scatter",
        data: points,
        symbolSize: (point: unknown[]) => Math.max(6, Math.min(30, Number(point[2] ?? 8))),
      },
    ],
  };
}

function scatterPoints(data: CategoryData, options: WidgetOptions): Array<[number, number, number, string]> {
  const points = (data as { points?: Array<{ x: number; y: number; size?: number; label?: string }> }).points;
  if (points) return points.map((point) => [point.x, point.y, point.size ?? 8, point.label ?? ""]);
  if (!data.columns || !data.rows) return [];
  const xIndex = columnIndex(data.columns, [options.x]);
  const yIndex = columnIndex(data.columns, [options.y, options.value]);
  const sizeIndex = columnIndex(data.columns, [options.size]);
  const labelIndex = columnIndex(data.columns, [options.label, options.category]);
  return data.rows
    .map((row) => {
      const x = numericValue(row[xIndex >= 0 ? xIndex : 0]);
      const y = numericValue(row[yIndex >= 0 ? yIndex : 1]);
      if (x === null || y === null) return null;
      return [
        x,
        y,
        sizeIndex >= 0 ? numericValue(row[sizeIndex]) ?? 8 : 8,
        labelIndex >= 0 ? cellText(row[labelIndex]) : "",
      ] as [number, number, number, string];
    })
    .filter((point): point is [number, number, number, string] => point !== null);
}

function buildHeatmapOption(data: CategoryData, options: WidgetOptions): Record<string, unknown> {
  const heatmap = heatmapData(data, options);
  return {
    grid: { left: 12, right: 24, top: 16, bottom: 42, containLabel: true },
    tooltip: { position: "top" },
    xAxis: { type: "category", data: heatmap.xLabels, ...baseAxis },
    yAxis: { type: "category", data: heatmap.yLabels, ...baseAxis },
    visualMap: {
      min: 0,
      max: Math.max(1, ...heatmap.values.map((item) => item[2])),
      calculable: false,
      orient: "horizontal",
      left: "center",
      bottom: 0,
      inRange: { color: ["#F7E7DE", EMBER, "#0E0E0E"] },
    },
    series: [{ type: "heatmap", data: heatmap.values, label: { show: false } }],
  };
}

function heatmapData(data: CategoryData, options: WidgetOptions) {
  const direct = data as {
    xLabels?: string[];
    yLabels?: string[];
    values?: Array<[string | number, string | number, number]>;
  };
  if (direct.xLabels && direct.yLabels && direct.values) {
    return {
      xLabels: direct.xLabels,
      yLabels: direct.yLabels,
      values: direct.values.map(([x, y, value]) => [
        typeof x === "number" ? x : direct.xLabels?.indexOf(String(x)) ?? 0,
        typeof y === "number" ? y : direct.yLabels?.indexOf(String(y)) ?? 0,
        value,
      ]) as Array<[number, number, number]>,
    };
  }
  if (!data.columns || !data.rows) return { xLabels: [], yLabels: [], values: [] };
  const categoryIndex = inferCategoryIndex(data.columns, data.rows, options);
  const numericColumns = data.columns
    .map((column, index) => ({ column, index }))
    .filter(
      ({ index }) =>
        index !== categoryIndex &&
        data.rows?.some((row) => numericValue(row[index]) !== null),
    );
  if (numericColumns.length > 1) {
    return {
      xLabels: numericColumns.map(({ column }) => column),
      yLabels: data.rows.map((row) => cellText(row[categoryIndex])),
      values: data.rows.flatMap((row, y) =>
        numericColumns.map(({ index }, x) => [x, y, numericValue(row[index]) ?? 0] as [number, number, number]),
      ),
    };
  }
  const xIndex = columnIndex(data.columns, [options.x, options.category]);
  const yIndex = columnIndex(data.columns, [options.y, options.group]);
  const valueIndex = columnIndex(data.columns, [options.value]);
  const rows = data.rows
    .map((row) => {
      const value = numericValue(row[valueIndex >= 0 ? valueIndex : 2]);
      if (value === null) return null;
      return {
        x: cellText(row[xIndex >= 0 ? xIndex : 0]),
        y: cellText(row[yIndex >= 0 ? yIndex : 1]),
        value,
      };
    })
    .filter((row): row is { x: string; y: string; value: number } => row !== null);
  const xLabels = Array.from(new Set(rows.map((row) => row.x)));
  const yLabels = Array.from(new Set(rows.map((row) => row.y)));
  return {
    xLabels,
    yLabels,
    values: rows.map((row) => [xLabels.indexOf(row.x), yLabels.indexOf(row.y), row.value] as [number, number, number]),
  };
}

function buildBoxplotOption(data: CategoryData, options: WidgetOptions): Record<string, unknown> {
  const boxplot = boxplotData(data, options);
  return {
    color: PALETTE,
    grid: { left: 12, right: 16, top: 16, bottom: 28, containLabel: true },
    tooltip: { trigger: "item" },
    xAxis: { type: "category", data: boxplot.categories, ...baseAxis },
    yAxis: valueAxis,
    series: [{ type: "boxplot", data: boxplot.values }],
  };
}

function boxplotData(data: CategoryData, options: WidgetOptions) {
  const direct = data as { items?: Array<{ name: string; values: number[] }> };
  if (direct.items?.every((item) => Array.isArray(item.values))) {
    return { categories: direct.items.map((item) => item.name), values: direct.items.map((item) => item.values) };
  }
  if (!data.columns || !data.rows) return { categories: [], values: [] };
  const categoryIndex = inferCategoryIndex(data.columns, data.rows, options);
  const numericColumns = data.columns
    .map((column, index) => ({ column, index }))
    .filter(({ index }) => index !== categoryIndex && data.rows?.some((row) => numericValue(row[index]) !== null));
  if (numericColumns.length >= 5) {
    return {
      categories: data.rows.map((row) => cellText(row[categoryIndex])),
      values: data.rows.map((row) => numericColumns.slice(0, 5).map(({ index }) => numericValue(row[index]) ?? 0)),
    };
  }
  const valueIndex = inferValueIndex(data.columns, data.rows, categoryIndex, options);
  const grouped = new Map<string, number[]>();
  data.rows.forEach((row) => {
    const value = numericValue(row[valueIndex]);
    if (value === null) return;
    const category = cellText(row[categoryIndex]);
    grouped.set(category, [...(grouped.get(category) ?? []), value]);
  });
  return {
    categories: Array.from(grouped.keys()),
    values: Array.from(grouped.values()).map(fiveNumberSummary),
  };
}

function fiveNumberSummary(values: number[]) {
  const sorted = [...values].sort((a, b) => a - b);
  return [
    sorted[0] ?? 0,
    quantile(sorted, 0.25),
    quantile(sorted, 0.5),
    quantile(sorted, 0.75),
    sorted[sorted.length - 1] ?? 0,
  ];
}

function quantile(sorted: number[], q: number) {
  if (sorted.length === 0) return 0;
  const pos = (sorted.length - 1) * q;
  const base = Math.floor(pos);
  const rest = pos - base;
  return (sorted[base] ?? 0) + rest * ((sorted[base + 1] ?? sorted[base] ?? 0) - (sorted[base] ?? 0));
}

function buildWaterfallOption(data: CategoryData, options: WidgetOptions): Record<string, unknown> {
  const normalized = categoryDataFromTable(data, options);
  const categories = normalized.categories ?? normalized.labels ?? [];
  const values = normalized.values ?? [];
  let total = 0;
  const helper = values.map((value) => {
    const start = total;
    total += value;
    return value >= 0 ? start : total;
  });
  return {
    grid: { left: 12, right: 16, top: 16, bottom: 28, containLabel: true },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" } },
    xAxis: { type: "category", data: categories, ...baseAxis },
    yAxis: valueAxis,
    series: [
      {
        type: "bar",
        stack: "total",
        itemStyle: { borderColor: "transparent", color: "transparent" },
        emphasis: { itemStyle: { borderColor: "transparent", color: "transparent" } },
        data: helper,
      },
      {
        type: "bar",
        stack: "total",
        data: values,
        itemStyle: { color: (params: { value: number }) => (params.value >= 0 ? EMBER : "#4B5563") },
      },
    ],
  };
}

function buildSankeyOption(data: CategoryData, options: WidgetOptions): Record<string, unknown> {
  const sankey = sankeyData(data, options);
  return {
    color: PALETTE,
    tooltip: { trigger: "item", triggerOn: "mousemove" },
    series: [
      {
        type: "sankey",
        left: 4,
        right: 96,
        top: 12,
        bottom: 12,
        nodeWidth: 12,
        nodeGap: 8,
        data: sankey.nodes,
        links: sankey.links,
        lineStyle: { color: "gradient", curveness: 0.45 },
        label: { color: INK, fontSize: 11, overflow: "truncate", width: 86 },
      },
    ],
  };
}

function sankeyData(data: CategoryData, options: WidgetOptions) {
  const direct = data as { nodes?: Array<{ name: string }>; links?: Array<{ source: string; target: string; value: number }> };
  if (direct.nodes && direct.links) return { nodes: direct.nodes, links: direct.links };
  if (!data.columns || !data.rows) return { nodes: [], links: [] };
  const sourceIndex = columnIndex(data.columns, [options.source, "source"]);
  const targetIndex = columnIndex(data.columns, [options.target, "target"]);
  const valueIndex = columnIndex(data.columns, [options.value]);
  const links = data.rows
    .map((row) => {
      const value = numericValue(row[valueIndex >= 0 ? valueIndex : 2]);
      if (value === null) return null;
      return {
        source: cellText(row[sourceIndex >= 0 ? sourceIndex : 0]),
        target: cellText(row[targetIndex >= 0 ? targetIndex : 1]),
        value,
      };
    })
    .filter((link): link is { source: string; target: string; value: number } => link !== null);
  const nodes = Array.from(new Set(links.flatMap((link) => [link.source, link.target]))).map((name) => ({ name }));
  return { nodes, links };
}

function buildTimelineOption(data: CategoryData, options: WidgetOptions): Record<string, unknown> {
  const timeline = timelineData(data, options);
  return {
    grid: { left: 12, right: 16, top: 16, bottom: 28, containLabel: true },
    tooltip: {
      trigger: "item",
      formatter: (params: { data?: unknown[] }) => {
        const point = params.data ?? [];
        return `${point[2] ?? ""}<br/>${point[0]}`;
      },
    },
    xAxis: { type: "time", ...baseAxis },
    yAxis: { type: "category", data: timeline.groups, ...baseAxis },
    series: [
      {
        type: "scatter",
        data: timeline.points,
        symbolSize: 10,
        itemStyle: { color: EMBER },
      },
    ],
  };
}

function timelineData(data: CategoryData, options: WidgetOptions) {
  const direct = data as { items?: Array<{ time: string; label?: string; group?: string }> };
  if (direct.items?.every((item) => "time" in item)) {
    const groups = Array.from(new Set(direct.items.map((item) => item.group ?? "events")));
    return {
      groups,
      points: direct.items.map((item) => [item.time, groups.indexOf(item.group ?? "events"), item.label ?? item.time]),
    };
  }
  if (!data.columns || !data.rows) return { groups: [], points: [] };
  const timeIndex = columnIndex(data.columns, [options.time, options.x, "time", "date"]);
  const labelIndex = columnIndex(data.columns, [options.label, "label", "event"]);
  const groupIndex = columnIndex(data.columns, [options.group, options.category]);
  const groups = Array.from(new Set(data.rows.map((row) => cellText(row[groupIndex >= 0 ? groupIndex : -1]) || "events")));
  return {
    groups,
    points: data.rows.map((row) => {
      const group = cellText(row[groupIndex >= 0 ? groupIndex : -1]) || "events";
      return [
        cellText(row[timeIndex >= 0 ? timeIndex : 0]),
        groups.indexOf(group),
        labelIndex >= 0 ? cellText(row[labelIndex]) : group,
      ];
    }),
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
  variant,
}: {
  data: CategoryData;
  options: WidgetOptions;
  height?: number;
  variant?: "bar" | "horizontal_bar" | "grouped_bar" | "stacked_bar";
}) {
  const resolvedVariant = resolveBarVariant(variant ?? "bar", data, options);
  const [limit, setLimit] = useState(10);
  const normalizedData = useMemo(
    () => categoryDataFromTable(data, options),
    [data, options],
  );
  const shouldFilter = !data.series && resolvedVariant !== "grouped_bar" && resolvedVariant !== "stacked_bar";
  const items = useMemo(() => chartItems(normalizedData), [normalizedData]);
  const visible = useMemo(() => limitItems(items, limit), [items, limit]);
  const chartData =
    shouldFilter && items.length > 0
      ? {
          categories: visible.map((item) => item.name),
          values: visible.map((item) => item.value),
        }
      : data;
  return (
    <>
      {options.interactive && shouldFilter ? (
        <div className="mb-2 flex justify-end">
          <ChartFilter count={items.length} limit={limit} onChange={setLimit} />
        </div>
      ) : null}
      <EChart
        height={height ?? (resolvedVariant === "horizontal_bar" || options.horizontal ? 300 : 240)}
        option={buildBarOption(chartData, options, resolvedVariant)}
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

function ChartShell({
  title,
  caption,
  children,
}: {
  title: string;
  caption?: string | null;
  children: React.ReactNode;
}) {
  return (
    <div className="py-4">
      <div className="mb-2 flex items-baseline justify-between">
        <h3 className="small-caps text-xs text-neutral-500">{title}</h3>
      </div>
      {children}
      {caption ? <Caption caption={caption} /> : null}
    </div>
  );
}

export function WidgetRenderer({ widget, height }: Props) {
  const kind = resolveWidgetKind(widget);
  const { title, data, caption, options } = widget;
  const widgetOptions = options as WidgetOptions;

  if (kind === "kpi") {
    return <KpiCard title={title} data={data as KpiData} caption={caption} />;
  }

  if (kind === "bar" || kind === "horizontal_bar" || kind === "grouped_bar" || kind === "stacked_bar") {
    const barOptions = {
      ...widgetOptions,
      horizontal:
        kind === "horizontal_bar" ||
        widgetOptions.horizontal ||
        widgetOptions.orientation === "horizontal" ||
        widgetOptions.layout === "horizontal",
    };
    return (
      <ChartShell title={title} caption={caption}>
        <FilterableBar
          data={data as CategoryData}
          options={barOptions}
          height={height}
          variant={kind}
        />
      </ChartShell>
    );
  }

  if (kind === "line" || kind === "area" || kind === "stacked_area") {
    return (
      <ChartShell title={title} caption={caption}>
        <EChart
          height={height}
          option={buildLineOption(data as CategoryData, widgetOptions, kind)}
        />
      </ChartShell>
    );
  }

  if (kind === "pie") {
    return (
      <ChartShell title={title} caption={caption}>
        <FilterablePie
          data={pieDataFromTable(data as PieData & CategoryData, widgetOptions) as PieData & CategoryData}
          height={height}
        />
      </ChartShell>
    );
  }

  if (kind === "histogram") {
    return (
      <ChartShell title={title} caption={caption}>
        <EChart height={height} option={buildBarOption(histogramData(data as CategoryData, widgetOptions))} />
      </ChartShell>
    );
  }

  if (kind === "scatter") {
    return (
      <ChartShell title={title} caption={caption}>
        <EChart height={height} option={buildScatterOption(data as CategoryData, widgetOptions)} />
      </ChartShell>
    );
  }

  if (kind === "heatmap") {
    return (
      <ChartShell title={title} caption={caption}>
        <EChart height={height} option={buildHeatmapOption(data as CategoryData, widgetOptions)} />
      </ChartShell>
    );
  }

  if (kind === "boxplot") {
    return (
      <ChartShell title={title} caption={caption}>
        <EChart height={height} option={buildBoxplotOption(data as CategoryData, widgetOptions)} />
      </ChartShell>
    );
  }

  if (kind === "waterfall") {
    return (
      <ChartShell title={title} caption={caption}>
        <EChart height={height} option={buildWaterfallOption(data as CategoryData, widgetOptions)} />
      </ChartShell>
    );
  }

  if (kind === "sankey") {
    return (
      <ChartShell title={title} caption={caption}>
        <EChart height={height} option={buildSankeyOption(data as CategoryData, widgetOptions)} />
      </ChartShell>
    );
  }

  if (kind === "timeline") {
    return (
      <ChartShell title={title} caption={caption}>
        <EChart height={height} option={buildTimelineOption(data as CategoryData, widgetOptions)} />
      </ChartShell>
    );
  }

  if (kind === "table") {
    return (
      <ChartShell title={title} caption={caption}>
        <TableWidget data={data as TableData} searchable={widgetOptions.searchable} />
      </ChartShell>
    );
  }

  if (kind === "markdown") {
    return (
      <ChartShell title={title} caption={caption}>
        <MarkdownWidget text={(data as MarkdownData).text ?? ""} />
      </ChartShell>
    );
  }

  return (
    <ChartShell title={title}>
      <div className="text-xs text-neutral-500">unknown widget kind: {kind}</div>
    </ChartShell>
  );
}
