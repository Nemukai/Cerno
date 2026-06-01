import { useEffect, useRef } from "react";
import { echarts } from "../lib/echarts";

type Props = {
  option: Record<string, unknown>;
  height?: number;
  onClick?: (params: Record<string, unknown>) => void;
};

export function EChart({ option, height = 240, onClick }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<ReturnType<typeof echarts.init> | null>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = echarts.init(ref.current, null, { renderer: "canvas" });
    chartRef.current = chart;
    let resizeTimer: number | undefined;
    const observer = new ResizeObserver(() => {
      window.clearTimeout(resizeTimer);
      resizeTimer = window.setTimeout(() => chart.resize(), 120);
    });
    observer.observe(ref.current);
    return () => {
      window.clearTimeout(resizeTimer);
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    chart.setOption(option, true);
    // Re-apply after the browser has settled the container size. Layout-based
    // series (e.g. graph) compute absolute positions at setOption time, so a
    // stale width on first paint would otherwise pin them to a corner.
    const raf = requestAnimationFrame(() => {
      chart.resize();
      chart.setOption(option, true);
    });
    return () => cancelAnimationFrame(raf);
  }, [option]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onClick) return;
    const handleClick = (params: unknown) => {
      if (params && typeof params === "object") {
        onClick(params as Record<string, unknown>);
      }
    };
    chart.on("click", handleClick);
    return () => {
      chart.off("click", handleClick);
    };
  }, [onClick]);

  return <div ref={ref} style={{ width: "100%", height }} />;
}
