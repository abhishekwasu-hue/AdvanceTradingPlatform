// N2-5: pure helpers for the market context panel. The server reads every value and says per tile whether it is ok,
// stale or not available; this only turns tiles into lines. Facts only.
import type { TFunction } from "i18next";
import type { ContextEvent, ContextTile, DowRead } from "../types";
import { formatNumber, formatPct } from "../ui/format";

export const TILE_ORDER = ["regime", "breadth", "vix", "fii_dii", "global", "events"] as const;

const num = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null);
const signedPct = (v: unknown): string => {
  const n = num(v);
  return n == null ? "-" : formatPct(n);
};

/** The lines a tile shows (empty for an NA tile: the card shows the reason instead). */
export function tileLines(key: string, tile: ContextTile, t: TFunction<"progression">): string[] {
  if (tile.status === "na") return [];
  switch (key) {
    case "regime": {
      const d = tile.daily as DowRead, w = tile.weekly as DowRead;
      const swings = (d?.swings ?? []).map((s) => `${s.label ?? s.kind} ${formatNumber(s.price, 3, { trim: true })} (${s.date})`).join(", ");
      return [t("context.regimeLine", { market: String(tile.market ?? ""), close: formatNumber(num(tile.close) ?? 0, 3, { trim: true }),
                                        daily: t(`context.trend.${d?.trend ?? "RANGE"}`), weekly: t(`context.trend.${w?.trend ?? "RANGE"}`) }),
              ...(swings ? [t("context.swings", { list: swings })] : [])];
    }
    case "breadth":
      return [t("context.breadthLine", { up: num(tile.advances) ?? 0, down: num(tile.declines) ?? 0, flat: num(tile.unchanged) ?? 0, count: num(tile.counted) ?? 0 }),
              ...(num(tile.ratio) != null ? [t("context.breadthRatio", { ratio: formatNumber(num(tile.ratio), 2) })] : [])];
    case "vix": {
      const out: string[] = [];
      if (num(tile.close) != null) {
        out.push(t("context.vixLine", { close: formatNumber(num(tile.close), 2), change: signedPct(tile.change_pct) }));
        out.push(t("context.vixRank", { rank: num(tile.rank) ?? "-", percentile: num(tile.percentile) ?? "-", sessions: num(tile.sessions) ?? 0 }));
      }
      const live = tile.live as { last: number; change_pct: number | null } | undefined;
      if (live) out.push(t("context.vixLive", { last: formatNumber(live.last, 2), change: signedPct(live.change_pct) }));
      return out;
    }
    case "global": {
      const rows = (tile.markets as { name: string; last: number | null; change_pct: number | null }[] | undefined) ?? [];
      const out = rows.map((r) => `${r.name}: ${r.last == null ? "-" : formatNumber(r.last, 3, { trim: true })} (${signedPct(r.change_pct)})`);
      if (num(tile.missing)) out.push(t("context.globalMissing", { count: num(tile.missing) }));
      return out;
    }
    case "events": {
      const row = (e: ContextEvent) => t("context.eventRow", { date: e.date, time: e.start_time ?? "", kind: e.kind, scope: e.scope === "*" ? "all" : e.scope,
                                                                description: e.description }) + (e.own ? ` · ${t("context.own")}` : "");
      const today = (tile.today as ContextEvent[] | undefined) ?? [], week = (tile.this_week as ContextEvent[] | undefined) ?? [];
      if (!today.length && !week.length) return [t("context.eventsNone")];
      return [...(today.length ? [`${t("context.eventsToday")}:`, ...today.map(row)] : []), ...(week.length ? [`${t("context.eventsWeek")}:`, ...week.map(row)] : [])];
    }
    default:
      return Object.entries(tile).filter(([k]) => !["status", "source", "as_of", "reason"].includes(k)).map(([k, v]) => `${k}: ${String(v)}`);
  }
}
