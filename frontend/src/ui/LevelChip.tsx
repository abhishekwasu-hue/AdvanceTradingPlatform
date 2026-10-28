import { Chip } from "./Chip";
import { LEVELS, type LevelScale } from "./levels";

/**
 * UX-11b C6: a level on one of the shared band tables (src/ui/levels.ts) - India VIX (Low / Normal / Elevated / High), the
 * global mood (Positive / Negative / Mixed) and a budget's use (Inside / Near the limit / Limit reached). Tone, mark and
 * word come from the table, so the backend and every page say the same thing.
 */
export function LevelChip<S extends LevelScale>({ scale, level, label, className }: {
  scale: S; level: keyof (typeof LEVELS)[S] | null | undefined; label?: string; className?: string;
}) {
  if (level == null) return null;
  const m = (LEVELS[scale] as Record<string, { tone: "up" | "down" | "warn" | "neutral"; mark: string; label: string }>)[String(level)];
  if (!m) return null;
  return (
    <Chip tone={m.tone} variant="meaning" icon={m.mark} name={`${scale}-${String(level).toLowerCase()}`} className={className}>
      {label ?? m.label}
    </Chip>
  );
}
