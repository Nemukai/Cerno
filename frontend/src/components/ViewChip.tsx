type Props = {
  label: string;
  onClick: () => void;
};

export function ViewChip({ label, onClick }: Props) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1.5 border border-ink bg-white px-2 py-1 text-xs text-ink hover:bg-neutral-100"
    >
      <span className="text-ember">{"\u2197"}</span>
      <span className="small-caps">{label}</span>
    </button>
  );
}
