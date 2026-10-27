import { useEffect, useState } from "react";
import { Link } from "react-router";
import { fetchOverview, type MarketGroup, type MarketsOverview } from "../markets/model";
import { formatPct } from "../ui/format";
import { CardV2, toneClass } from "./CardV2";

/**
 * TD-1 (owner, 25 Oct; UX-10 wave 1, Today): "Your week" as small tiles instead of sentences - NIFTY 50 week %,
 * BANK NIFTY week %, the best and the worst sector of the week. One number and one name per tile, colour by meaning
 * (up / down) and the sign, never colour alone. The numbers are the Sectors & indices page's own (`/markets/overview`,
 * flag `markets_view`); without that data the card is not shown. A tile opens that page on its Week view.
 */
export interface WeekTile { key: string; label: string; value: number | null; caption?: string }

const NIFTY = new Set(["NIFTY 50", "NIFTY"]);
const BANK = new Set(["NIFTY BANK", "BANKNIFTY", "BANK NIFTY"]);

const byName = (groups: readonly MarketGroup[], names: Set<string>) =>
  groups.find((g) => names.has((g.symbol ?? g.name).toUpperCase()) || names.has(g.name.toUpperCase())) ?? null;

/** The four tiles from the overview; [] when the week's numbers are not known. Pure. */
export function weekTiles(o: MarketsOverview | null): WeekTile[] {
  if (!o || o.state !== "ok") return [];
  const nifty = byName(o.indices, NIFTY);
  const bank = byName(o.indices, BANK);
  const sectors = o.sectors.filter((s) => typeof s.change.week === "number" && Number.isFinite(s.change.week));
  const sorted = [...sectors].sort((a, b) => (b.change.week as number) - (a.change.week as number));
  const best = sorted[0] ?? null;
  const worst = sorted.length > 1 ? sorted[sorted.length - 1] : null;
  const tiles: WeekTile[] = [
    { key: "nifty", label: "NIFTY 50", value: nifty?.change.week ?? null },
    { key: "bank", label: "BANK NIFTY", value: bank?.change.week ?? null },
    { key: "best", label: "Best sector", value: best?.change.week ?? null, caption: best?.name },
    { key: "worst", label: "Weakest sector", value: worst?.change.week ?? null, caption: worst?.name },
  ];
  return tiles.some((t) => t.value != null) ? tiles : [];
}

const tone = (v: number | null) => (v == null || v === 0 ? "neutral" : v > 0 ? "up" : "down");

export function WeekTilesView({ tiles }: { tiles: readonly WeekTile[] }) {
  if (tiles.length === 0) return null;
  return (
    <CardV2 title="This week" more={{ label: "Sectors", to: "/markets?period=week" }} testId="week-tiles">
      <div className="grid grid-cols-2 gap-space-3">
        {tiles.map((t) => (
          <Link key={t.key} to="/markets?period=week" data-week-tile={t.key}
            className="min-w-0 rounded-control bg-surface-2 p-space-3 hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
            <p className="truncate text-t12 font-medium text-fg-muted">{t.label}</p>
            <p className={`mt-px text-t18 font-semibold tabular-nums ${toneClass(tone(t.value))}`}>{formatPct(t.value)}</p>
            {t.caption && <p className="truncate text-t12 text-fg-muted">{t.caption}</p>}
          </Link>
        ))}
      </div>
    </CardV2>
  );
}

/** Reads the overview once while shown; any failure (flag off, no data yet) shows nothing. */
export function WeekTilesV2({ enabled }: { enabled: boolean }) {
  const [overview, setOverview] = useState<MarketsOverview | null>(null);
  useEffect(() => {
    if (!enabled) return undefined;
    let live = true;
    fetchOverview().then((o) => { if (live) setOverview(o); }, () => { if (live) setOverview(null); });
    return () => { live = false; };
  }, [enabled]);
  return enabled ? <WeekTilesView tiles={weekTiles(overview)} /> : null;
}
