export type NumberSystem = "international" | "indian";

const NUMBER_LOCALES: Record<NumberSystem, string> = {
  international: "en-US",
  indian: "en-IN",
};

export function formatNumber(value: number, system: NumberSystem): string {
  if (!Number.isFinite(value)) return String(value);
  return new Intl.NumberFormat(NUMBER_LOCALES[system], {
    maximumFractionDigits: Number.isInteger(value) ? 0 : 20,
  }).format(value);
}
