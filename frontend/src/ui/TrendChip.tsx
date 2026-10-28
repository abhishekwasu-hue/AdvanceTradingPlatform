import { Chip } from "./Chip";
import { TREND, type Trend } from "./levels";

/**
 * UX-11b C6: a swing sequence as a chip - Up ▲ green, Down ▼ red, Sideways ◆ grey (the one colour table, src/ui/levels.ts).
 * The mark and the word carry the meaning; the colour repeats it. A structure word, never a call. DirectionChip stays for
 * a position's or signal's long / short.
 */
export function TrendChip({ trend, label, className }: { trend: Trend | null | undefined; label?: string; className?: string }) {
  const m = TREND[trend ?? "SIDEWAYS"] ?? TREND.SIDEWAYS;
  return (
    <Chip tone={m.tone} variant="meaning" icon={m.mark} name={`trend-${(trend ?? "SIDEWAYS").toLowerCase()}`} className={className}>
      {label ?? m.label}
    </Chip>
  );
}
