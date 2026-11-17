import type { LayerBand } from "../../charting/engine";
import type { ChartMarker } from "../../charting/markers";
import { ZONE_COLORS } from "../../charting/marketZones";
import { areaLabelText } from "../../charting/geometry";
import type { LevelLine } from "../../chart/chartCore";
import type { AtpReading, AtpReadingTrend } from "../../chart/atpReading";
import { cardTrendText, headlineAreas, type AnswerHeadline, type CardArea, type StructureCard } from "./model";

/** A band's look by its stored state (as the Charts tab's zones layer): fresh solid, tested dashed, flipped dotted. */
const STATE_LOOK: Record<string, { opacity: number; style: "solid" | "dashed" | "dotted" }> = {
  tested: { opacity: 0.11, style: "dashed" },
  flipped: { opacity: 0.11, style: "dotted" },
};
const FRESH = { opacity: 0.18, style: "solid" as const };

/** One card area as a band: buyer / seller colour by its role at the price the answer read it at (the server's
 * `role_at`), labelled like every band layer ("Seller area · 1w context · tested"). Pure. */
export function areaBand(a: CardArea): LayerBand | null {
  if (!Number.isFinite(a.from) || !Number.isFinite(a.to)) return null;
  const look = (a.state && STATE_LOOK[a.state.toLowerCase()]) || FRESH;
  const timeframe = a.timeframe ? (a.context ? `${a.timeframe} context` : a.timeframe) : null;
  return { low: Math.min(a.from, a.to), high: Math.max(a.from, a.to), role: a.side ?? "neutral", color: a.side ? ZONE_COLORS[a.side] : undefined,
           ...look, label: areaLabelText({ role: a.side, timeframe, state: a.state }) };
}

function epochIso(t: number): string {
  return new Date(t * 1000).toISOString();
}

/**
 * Owner 15 Nov: the answer's own reading as chart items - nothing the answer does not hold:
 * - each timeframe's protected level as a dashed line "Weekly protected" (one line per price: "Weekly · Daily protected"),
 *   green under an up trend, red under a down trend, neutral otherwise;
 * - each timeframe's last break as a marker at its own bar ("Weekly · Break against the trend"), only when the answer
 *   carries that bar (an older answer: no marker, never a guessed bar);
 * - the areas above and below as bands (the answer without a card: its one nearest area);
 * - the trend per timeframe for the legend (a timeframe not ready is left out).
 * Structure words only: no buy / sell / target / stop wording. Pure.
 */
export function readingOverlay(card: StructureCard | null | undefined, headline?: AnswerHeadline | null): AtpReading {
  const levels: LevelLine[] = [];
  const markers: ChartMarker[] = [];
  const trends: AtpReadingTrend[] = [];
  const byPrice = new Map<number, { labels: string[]; tone: LevelLine["tone"] }>();
  for (const row of card?.timeframes ?? []) {
    if (!row.ready) continue;
    if (row.trend) trends.push({ tf: row.tf, label: row.label, trend: row.trend, weak: row.weak, text: cardTrendText(row) });
    if (row.protected != null && Number.isFinite(row.protected)) {
      const tone: LevelLine["tone"] = row.trend === "up" ? "up" : row.trend === "down" ? "down" : "neutral";
      const same = byPrice.get(row.protected);
      if (same) { same.labels.push(row.label); if (same.tone !== tone) same.tone = "neutral"; }
      else byPrice.set(row.protected, { labels: [row.label], tone });
    }
    const brk = row.last_break;
    if (brk && typeof brk.time === "number" && Number.isFinite(brk.time)) {
      const up = brk.direction === "up", down = brk.direction === "down";
      markers.push({ timestamp: epochIso(brk.time), position: up ? "belowBar" : "aboveBar", color: up ? "up" : down ? "down" : "fg",
                     shape: up ? "arrowUp" : down ? "arrowDown" : "circle", text: `${row.label} · ${brk.label}` });
    }
  }
  for (const [price, { labels, tone }] of byPrice) levels.push({ price, title: `${labels.join(" · ")} protected`, tone });
  const areas: LayerBand[] = [];
  const seen = new Set<string>();
  const add = (b: LayerBand | null) => {
    if (!b) return;
    const key = `${b.low}|${b.high}`;
    if (seen.has(key)) return;
    seen.add(key);
    areas.push(b);
  };
  for (const a of [...(card?.areas_above ?? []), ...(card?.areas_below ?? [])]) add(areaBand(a));
  if (areas.length === 0) for (const b of headlineAreas(headline)) add(b);
  return { levels, areas, markers, trends };
}
