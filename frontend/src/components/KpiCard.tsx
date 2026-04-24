import type { KpiData } from "../lib/types";
import { DeltaPill } from "./DeltaPill";

type Props = {
  title: string;
  data: KpiData;
  caption?: string | null;
};

export function KpiCard({ title, data, caption }: Props) {
  return (
    <div className="py-4">
      <div className="small-caps text-xs text-neutral-500">{title}</div>
      <div className="mt-1 flex items-center gap-3">
        <div className="font-mono text-4xl font-medium tracking-tight text-ink">
          {data.value}
        </div>
        {data.delta ? (
          <DeltaPill direction={data.delta.direction} value={data.delta.value} />
        ) : null}
      </div>
      {data.label ? (
        <div className="mt-0.5 text-xs text-neutral-500">{data.label}</div>
      ) : null}
      {caption ? (
        <div className="mt-2 text-xs text-neutral-500">{caption}</div>
      ) : null}
    </div>
  );
}
