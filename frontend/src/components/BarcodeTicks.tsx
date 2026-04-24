type Props = {
  value: number;
  total?: number;
};

export function BarcodeTicks({ value, total = 10 }: Props) {
  const filled = Math.max(0, Math.min(total, Math.round(value * total)));
  return (
    <span className="inline-flex items-end gap-[2px] align-middle">
      {Array.from({ length: total }).map((_, i) => (
        <span
          key={i}
          className={`inline-block h-3 w-[2px] ${i < filled ? "bg-ink" : "bg-neutral-300"}`}
        />
      ))}
    </span>
  );
}
