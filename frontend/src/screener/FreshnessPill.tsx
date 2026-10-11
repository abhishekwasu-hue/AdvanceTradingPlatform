/**
 * U5: how fresh the scan's data is - the source and when it was read, as a pill in the header. It turns warn when the
 * data is older than the scan's own timeframe allows. The time is always the real one, never fixed text.
 */
import { cx } from "../components/primitives";

const TF_MS: Record<string, number> = {
  "1m": 60e3, "3m": 180e3, "5m": 300e3, "15m": 900e3, "30m": 1800e3, "1h": 3600e3, "1d": 86400e3, "1w": 7 * 86400e3, "1M": 31 * 86400e3,
};

/** Minutes the data is behind (null without a run); stale once more than two of the scan's bars old. Pure, for tests. */
export function freshness(ranAt: Date | null, now: Date, scanTf: string): { behindMin: number | null; stale: boolean } {
  if (!ranAt) return { behindMin: null, stale: false };
  const behind = Math.max(0, now.getTime() - ranAt.getTime());
  return { behindMin: Math.floor(behind / 60e3), stale: behind > 2 * (TF_MS[scanTf] ?? TF_MS["1d"]) };
}

export function formatIst(d: Date): string {
  return new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false }).format(d);
}

export interface FreshnessPillProps { ranAt: Date | null; source: string | null; scanTf: string; now?: Date }

export function FreshnessPill({ ranAt, source, scanTf, now = new Date() }: FreshnessPillProps) {
  const f = freshness(ranAt, now, scanTf);
  if (!ranAt) {
    return <span className="inline-flex h-6 items-center rounded-full border border-border px-2.5 text-t12 text-fg-muted">Not run yet</span>;
  }
  return (
    <span role="status" title={source ? `Data from ${source}` : undefined}
          className={cx("inline-flex h-6 items-center gap-1.5 rounded-full border px-2.5 text-t12",
            f.stale ? "border-warn/60 text-warn" : "border-border text-fg-muted")}>
      <span aria-hidden className={cx("h-1.5 w-1.5 rounded-full", f.stale ? "bg-warn" : "bg-signal")} />
      Data at <span className="font-mono font-tabular">{formatIst(ranAt)}</span> IST
      {f.stale && f.behindMin != null && <span> · behind by {f.behindMin} min</span>}
    </span>
  );
}
