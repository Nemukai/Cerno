import type { KpiData } from "../lib/types";
import { DeltaPill } from "./DeltaPill";

type Props = {
  title: string;
  data: KpiData;
  caption?: string | null;
};

export function KpiCard({ title, data, caption }: Props) {
  const items = (data.items ?? [{ ...data, title }]).slice(0, 4);

  return (
    <div className="py-4">
      {items.length === 1 ? (
        <div className="small-caps text-xs text-neutral-500">{items[0]?.title ?? title}</div>
      ) : (
        <div className="small-caps text-xs text-neutral-500">{title}</div>
      )}
      <div
        className={
          items.length === 1
            ? "mt-1 flex items-center gap-3"
            : "mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4"
        }
      >
        {items.map((item, index) => (
          <div
            key={`${item.title ?? title}-${index}`}
            className={items.length === 1 ? "contents" : "border border-neutral-200 bg-white px-3 py-2"}
          >
            {items.length > 1 ? (
              <div className="small-caps mb-1 text-[10px] text-neutral-500">
                {item.title ?? `metric ${index + 1}`}
              </div>
            ) : null}
            <div className="flex min-w-0 items-center gap-2">
              <div
                className={
                  items.length === 1
                    ? "font-mono text-4xl font-medium tracking-tight text-ink"
                    : "min-w-0 overflow-hidden text-ellipsis font-mono text-2xl font-medium text-ink"
                }
              >
                {item.value}
              </div>
              {item.delta ? (
                <DeltaPill direction={item.delta.direction} value={item.delta.value} />
              ) : null}
            </div>
            {item.label ? (
              <div className="mt-0.5 text-xs text-neutral-500">{item.label}</div>
            ) : null}
          </div>
        ))}
      </div>
      {caption ? (
        <div className="mt-2 text-xs text-neutral-500">{caption}</div>
      ) : null}
    </div>
  );
}
