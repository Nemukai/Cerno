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
    const observer = new ResizeObserver(() => chart.resize());
    observer.observe(ref.current);
    return () => {
      observer.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, []);

  useEffect(() => {
    if (!chartRef.current) return;
    chartRef.current.setOption(option, true);
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
