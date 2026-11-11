import { useEffect, useState } from "react";
import { request } from "../api/client";
import { CardV2 } from "../dashboard/CardV2";
import { Chip } from "../ui/Chip";
import { Details } from "../ui/Details";
import { formatNumber } from "../ui/format";

/**
 * M99 P7 (flags `ui_v2` + `risk_options`): the trader's own "not moving" flag on his open positions - a position whose
 * last N bars since its entry moved less than his own share of its entry price. Information only: nothing is closed,
 * blocked or changed by it (his own time exit, if he set one, is a separate exit setting). Shown only when it has
 * something to show.
 */

export interface StagnationItem {
  trade_id: number; symbol: string; read: string; bar_minutes: number;
  state: "not_moving" | "moving" | "too_few_bars" | "no_bars"; bars?: number; moved_pct?: number; limit_pct?: number; closed_bars?: number;
}
export interface StagnationView { on: boolean; bars?: number; move_pct?: number; bar_minutes?: number; positions: StagnationItem[]; note?: string }

export async function loadStagnation(fetcher: typeof request = request): Promise<StagnationView> {
  return fetcher<StagnationView>("/risk-settings/stagnation");
}

/** "Moved 0.4% in the last 3 bars of 5 min" - one line per flagged position. */
export function movedWords(p: StagnationItem): string {
  return `Moved ${formatNumber(p.moved_pct ?? 0, 2)}% in the last ${formatNumber(p.bars ?? 0)} bars of ${formatNumber(p.bar_minutes)} min`
    + (p.read !== p.symbol ? ` (${p.read})` : "");
}

export default function NotMovingV2({ refreshKey = 0, load = loadStagnation }: { refreshKey?: number; load?: typeof loadStagnation }) {
  const [view, setView] = useState<StagnationView | null>(null);
  useEffect(() => {
    let live = true;
    load().then((v) => { if (live) setView(v); }).catch(() => { if (live) setView(null); });
    return () => { live = false; };
  }, [load, refreshKey]);
  const flagged = (view?.on ? view.positions : []).filter((p) => p.state === "not_moving");
  if (!flagged.length) return null;
  return (
    <CardV2 title="Not moving" testId="not-moving"
      badge={<Chip tone="warn" variant="meaning" name="not-moving-count">{formatNumber(flagged.length)}</Chip>}>
      <ul className="divide-y divide-subtle">
        {flagged.map((p) => (
          <li key={p.trade_id} className="flex flex-wrap items-baseline justify-between gap-space-2 py-space-2" data-not-moving={p.trade_id}>
            <span className="min-w-0 truncate text-t14 font-medium text-fg">{p.symbol}</span>
            <span className="text-t12 text-fg-muted">{movedWords(p)}</span>
          </li>
        ))}
      </ul>
      <Details id="positions.not-moving" label="What it means" className="mt-space-1">
        <p>Your own setting: moved at most {formatNumber(view?.move_pct ?? 0, 2)}% of the entry price in {formatNumber(view?.bars ?? 0)} bars.
          {" "}{view?.note ?? "Nothing is closed by it."}</p>
      </Details>
    </CardV2>
  );
}
