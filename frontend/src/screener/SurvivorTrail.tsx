/**
 * U5: the survivor trail on the right edge of a stage - how many symbols are still in after it, as a bar on a thin
 * recessed track plus the number, and how many it removed. The stage that removes the last symbols turns warn and
 * offers to show what it removes. No count yet (not run, or changed since) is a quiet dash, never a stale number. The
 * trail is not a live region: the canvas announces one summary per run, not a count per stage.
 */
import { cx } from "../components/primitives";

export interface SurvivorTrailProps {
  survivors: number | null;
  removed: number | null;
  total: number | null;          // the universe with data: the track's full width
  kills?: boolean;
  onShowRemoved?: () => void;
}

export function trailFraction(survivors: number | null, total: number | null): number {
  if (survivors == null || !total) return 0;
  return Math.max(0, Math.min(1, survivors / total));
}

export function SurvivorTrail({ survivors, removed, total, kills = false, onShowRemoved }: SurvivorTrailProps) {
  const known = survivors != null;
  const pct = trailFraction(survivors, total) * 100;
  return (
    <div className="flex w-28 shrink-0 flex-col items-end gap-1">
      <div className="flex items-baseline gap-1.5">
        {known && removed != null && removed > 0 && (
          <span className="font-mono font-tabular text-t12 text-fg-muted" aria-label={`removes ${removed}`}>−{removed}</span>
        )}
        <span className={cx("font-mono font-tabular text-t15 font-semibold", !known ? "text-fg-muted" : kills ? "text-warn" : "text-signal")}
              aria-label={known ? `${survivors} symbols left` : "not counted yet"}>
          {known ? survivors : "–"}
        </span>
      </div>
      <div className="h-1 w-full overflow-hidden rounded-full bg-surface-inset" aria-hidden>
        <div className={cx("trail-fill h-full rounded-full", kills ? "bg-warn" : "bg-signal")} style={{ width: `${pct}%` }} />
      </div>
      {kills && onShowRemoved && (
        <button type="button" onClick={onShowRemoved}
                className="text-t12 text-warn underline underline-offset-2 hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-warn">
          See what this removes
        </button>
      )}
    </div>
  );
}
