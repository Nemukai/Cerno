import { useMemo, type ReactNode } from "react";
import { EChart } from "./EChart";
import { useTheme } from "@/lib/theme";

export function ChartPanel({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <div className="border border-border bg-card">
      <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
        <div className="font-hud text-[10px] text-primary">{title}</div>
        {subtitle ? <div className="font-hud text-[9px] text-foreground/40">{subtitle}</div> : null}
      </div>
      <div className="p-4">{children}</div>
    </div>
  );
}

type DayEvent = { occurred_at: string; event_name?: string };

function palette(dark: boolean) {
  return {
    accent: dark ? "#B7B1FF" : "#6E66C9",
    accentSoft: dark ? "rgba(183,177,255,0.18)" : "rgba(110,102,201,0.16)",
    signal: dark ? "#C9F24E" : "#7E9E2C",
    axis: dark ? "rgba(255,255,255,0.45)" : "rgba(20,18,30,0.5)",
    split: dark ? "rgba(255,255,255,0.06)" : "rgba(20,18,30,0.07)",
    bg: "transparent",
  };
}

// Events that are not deliberate product usage: auth churn (sign-in fires on every
// OAuth callback) and background worker/system jobs. Counting these makes the
// activity chart show usage on days with no real user activity.
const NON_ACTIVITY_EVENTS = new Set([
  "user_signed_in",
  "processing_job_queued",
  "processing_job_completed",
  "processing_job_failed",
]);

function bucketByDay(events: DayEvent[]): { day: string; count: number }[] {
  const map = new Map<string, number>();
  for (const e of events) {
    if (!e.occurred_at) continue;
    if (e.event_name && NON_ACTIVITY_EVENTS.has(e.event_name)) continue;
    const day = e.occurred_at.slice(0, 10);
    map.set(day, (map.get(day) ?? 0) + 1);
  }
  return [...map.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([day, count]) => ({ day, count }));
}

const FONT = "'JetBrains Mono', ui-monospace, monospace";

/** Activity over time: events per day as a soft area line. */
export function ActivityChart({ events, height = 220 }: { events: DayEvent[]; height?: number }) {
  const { theme } = useTheme();
  const dark = theme === "dark";
  const data = useMemo(() => bucketByDay(events), [events]);
  const option = useMemo(() => {
    const c = palette(dark);
    return {
      backgroundColor: c.bg,
      grid: { left: 36, right: 14, top: 16, bottom: 28 },
      tooltip: {
        trigger: "axis",
        backgroundColor: dark ? "#0b0a12" : "#ffffff",
        borderColor: c.split,
        textStyle: { color: dark ? "#e9e8f2" : "#15131c", fontFamily: FONT, fontSize: 11 },
      },
      xAxis: {
        type: "category",
        data: data.map((d) => d.day.slice(5)),
        axisLine: { lineStyle: { color: c.split } },
        axisTick: { show: false },
        axisLabel: { color: c.axis, fontFamily: FONT, fontSize: 9 },
      },
      yAxis: {
        type: "value",
        minInterval: 1,
        splitLine: { lineStyle: { color: c.split } },
        axisLabel: { color: c.axis, fontFamily: FONT, fontSize: 9 },
      },
      series: [
        {
          type: "line",
          data: data.map((d) => d.count),
          smooth: true,
          symbol: "circle",
          symbolSize: 5,
          lineStyle: { color: c.accent, width: 2 },
          itemStyle: { color: c.accent },
          areaStyle: { color: c.accentSoft },
        },
      ],
    };
  }, [data, dark]);

  if (data.length === 0) {
    return (
      <div
        className="flex items-center justify-center border border-dashed border-border font-hud text-[10px] text-muted-foreground"
        style={{ height }}
      >
        NO RECENT ACTIVITY
      </div>
    );
  }
  return <EChart option={option} height={height} />;
}

/** Horizontal comparison bars for top-N entities by a metric. */
export function TopBarChart({
  items,
  height = 220,
  useSignal = false,
}: {
  items: { name: string; value: number }[];
  height?: number;
  useSignal?: boolean;
}) {
  const { theme } = useTheme();
  const dark = theme === "dark";
  const option = useMemo(() => {
    const c = palette(dark);
    const top = [...items].sort((a, b) => b.value - a.value).slice(0, 8).reverse();
    return {
      backgroundColor: c.bg,
      grid: { left: 8, right: 18, top: 8, bottom: 8, containLabel: true },
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" },
        backgroundColor: dark ? "#0b0a12" : "#ffffff",
        borderColor: c.split,
        textStyle: { color: dark ? "#e9e8f2" : "#15131c", fontFamily: FONT, fontSize: 11 },
      },
      xAxis: {
        type: "value",
        splitLine: { lineStyle: { color: c.split } },
        axisLabel: { color: c.axis, fontFamily: FONT, fontSize: 9 },
      },
      yAxis: {
        type: "category",
        data: top.map((t) => t.name),
        axisLine: { lineStyle: { color: c.split } },
        axisTick: { show: false },
        axisLabel: { color: c.axis, fontFamily: FONT, fontSize: 10 },
      },
      series: [
        {
          type: "bar",
          data: top.map((t) => t.value),
          barWidth: "58%",
          itemStyle: { color: useSignal ? c.signal : c.accent },
        },
      ],
    };
  }, [items, dark, useSignal]);

  if (items.length === 0) {
    return (
      <div
        className="flex items-center justify-center border border-dashed border-border font-hud text-[10px] text-muted-foreground"
        style={{ height }}
      >
        NO DATA
      </div>
    );
  }
  return <EChart option={option} height={height} />;
}
