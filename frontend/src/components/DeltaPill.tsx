type Props = {
  direction: "up" | "down";
  value: string;
};

export function DeltaPill({ direction, value }: Props) {
  const glyph = direction === "up" ? "\u2197" : "\u2198";
  return (
    <span className="inline-flex items-center gap-1 bg-ember px-1.5 py-0.5 font-mono text-xs text-white">
      <span>{glyph}</span>
      <span>{value}</span>
    </span>
  );
}
