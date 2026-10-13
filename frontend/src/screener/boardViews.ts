/**
 * U5 D3a: the result board's heatmap and chart-grid views (flag `screener_board_views`; off -> the context call answers
 * 404 and only the table shows). Pure helpers: the context API, a squarified treemap (Bruls, Huizing, van Wijk) of the
 * scanned symbols grouped by sector and sized by market cap, the tile colours and sparkline paths. Every number comes
 * from the run and the server's context (company reference data, shared daily closes) - none from pixels.
 */
import { request } from "../api/client";
import type { RunResult } from "./api";

export type BoardView = "table" | "heatmap" | "grid" | "rrg";
export const BOARD_VIEWS: { id: BoardView; label: string }[] = [
  { id: "table", label: "Table" }, { id: "heatmap", label: "Heatmap" }, { id: "grid", label: "Chart grid" }, { id: "rrg", label: "RRG" },
];
export const GRID_MAX = 12;                       // mini charts in the grid (the spec's 12 / 20; 12 keeps evidence reads small)
export const RRG_MAX = 50;                        // tails on one RRG plot (the server's RRG_MAX_SYMBOLS)
export const CONTEXT_MAX = 500;                   // symbols per context call (the server's MAX_SYMBOLS: a NIFTY 500 scan in one call)

export interface ContextRow {
  symbol: string; sector: string; industry: string | null; market_cap: number | null; cap_category: string | null;
  closes: { t: number; c: number }[]; change_pct: number | null;
}
export interface BoardContext { as_of: string; exchange: string; symbols: ContextRow[]; note: string }

export interface RrgPoint { week: string; rs_ratio: number; rs_momentum: number; forming?: boolean }
export type Quadrant = "Leading" | "Weakening" | "Lagging" | "Improving";
export interface RrgResponse {
  as_of: string; benchmark: string; benchmark_key: string; weeks: number; reason: string | null; note: string; forming_week?: string | null;
  symbols: { symbol: string; tail: RrgPoint[]; quadrant: Quadrant | null }[];
}
export interface TemplateRun { run_id: number; at: string | null; base_tf: string; scanned: number; matched: number; evidence_id: number | null }

export const boardApi = {
  context: (symbols: string[], exchange = "NSE") =>
    request<BoardContext>("/screener/board/context", { method: "POST", body: JSON.stringify({ symbols, exchange }) }),
  /** D3b: relative rotation against a benchmark index (weekly, shared daily closes). */
  rrg: (symbols: string[], benchmark = "Nifty 50", exchange = "NSE") =>
    request<RrgResponse>("/screener/board/rrg", { method: "POST", body: JSON.stringify({ symbols, benchmark, exchange }) }),
  /** D3b: the organisation's latest stored run of each template (null when none) - the sheet's counts and thumbnail. */
  templateRuns: (templates: { id: string; source: string }[]) =>
    request<{ runs: Record<string, TemplateRun | null> }>("/screener/board/template-runs", { method: "POST", body: JSON.stringify({ templates }) }),
};

/** RRG plot scale: both axes symmetric around 100 so the quadrants meet in the middle (at least +/- 2 points). */
export function rrgScale(rows: RrgResponse["symbols"], w: number, h: number, pad = 18) {
  let span = 2;
  for (const r of rows) for (const p of r.tail) span = Math.max(span, Math.abs(p.rs_ratio - 100), Math.abs(p.rs_momentum - 100));
  span *= 1.1;
  return {
    span,
    x: (v: number) => pad + ((v - (100 - span)) / (2 * span)) * (w - 2 * pad),
    y: (v: number) => h - pad - ((v - (100 - span)) / (2 * span)) * (h - 2 * pad),
  };
}

/** "3 of 10 matched · 12 Oct, 15:30 IST" for a template's last run; null when it was never run. */
export function lastRunText(run: TemplateRun | null | undefined): string | null {
  if (!run) return null;
  const d = run.at ? new Date(run.at) : null;
  const when = d && !Number.isNaN(d.getTime())
    ? ` · ${d.toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", hour12: false })} IST` : "";
  return `${run.matched} of ${run.scanned} matched${when}`;
}

export interface Rect { x: number; y: number; w: number; h: number }
export interface Sized<T> { item: T; value: number }

function worst(row: number[], side: number): number {
  const sum = row.reduce((a, b) => a + b, 0);
  const max = Math.max(...row), min = Math.min(...row);
  return Math.max((side * side * max) / (sum * sum), (sum * sum) / (side * side * min));
}

/** Squarified treemap: rectangles for `items` (value > 0) filling `box`, largest first; areas proportional to value. */
export function squarify<T>(items: Sized<T>[], box: Rect): { item: T; rect: Rect }[] {
  const list = items.filter((i) => i.value > 0).sort((a, b) => b.value - a.value);
  const total = list.reduce((a, b) => a + b.value, 0);
  if (!list.length || box.w <= 0 || box.h <= 0) return [];
  const scale = (box.w * box.h) / total;
  const areas = list.map((i) => i.value * scale);
  const out: { item: T; rect: Rect }[] = [];
  let free = { ...box }, i = 0;
  while (i < areas.length) {
    const side = Math.min(free.w, free.h);
    const row = [areas[i]];
    let j = i + 1;
    while (j < areas.length && worst([...row, areas[j]], side) <= worst(row, side)) { row.push(areas[j]); j++; }
    const sum = row.reduce((a, b) => a + b, 0);
    if (free.w >= free.h) {                         // lay the row down the left edge
      const w = sum / free.h;
      let y = free.y;
      row.forEach((a, k) => { const h = a / w; out.push({ item: list[i + k].item, rect: { x: free.x, y, w, h } }); y += h; });
      free = { x: free.x + w, y: free.y, w: free.w - w, h: free.h };
    } else {                                        // along the top edge
      const h = sum / free.w;
      let x = free.x;
      row.forEach((a, k) => { const w = a / h; out.push({ item: list[i + k].item, rect: { x, y: free.y, w, h } }); x += w; });
      free = { x: free.x, y: free.y + h, w: free.w, h: free.h - h };
    }
    i = j;
  }
  return out;
}

export interface HeatTile { symbol: string; sector: string; matched: boolean; unread: boolean; change: number | null; cap: number | null; rect: Rect }
export interface HeatGroup { sector: string; rect: Rect; tiles: HeatTile[]; matched: number }

/** The results the heatmap lays out: those the server returned context for (the first CONTEXT_MAX), never a symbol
 * filed as "Unclassified" only because it was not asked about - the caption says "first N of M shown" instead. */
export function heatmapResults(results: RunResult[], ctx: ContextRow[]): RunResult[] {
  const have = new Set(ctx.map((c) => c.symbol));
  return results.filter((r) => have.has(r.symbol));
}

/** Sectors sized by their symbols' market caps, symbols inside each; a symbol with no market cap counts as the
 * median cap of those that have one (equal sizes when none has one), so it is still visible - said in the legend.
 * Only symbols with a context row are laid out (heatmapResults). */
export function heatmapLayout(results: RunResult[], ctx: ContextRow[], box: Rect): HeatGroup[] {
  const byS = new Map(ctx.map((c) => [c.symbol, c]));
  const caps = ctx.map((c) => c.market_cap).filter((v): v is number => v != null && v > 0).sort((a, b) => a - b);
  const fill = caps.length ? caps[Math.floor(caps.length / 2)] : 1;
  const sectors = new Map<string, { r: RunResult; c: ContextRow; v: number }[]>();
  for (const r of results) {
    const c = byS.get(r.symbol);
    if (!c) continue;
    const sector = c.sector;
    const v = c.market_cap != null && c.market_cap > 0 ? c.market_cap : fill;
    sectors.set(sector, [...(sectors.get(sector) ?? []), { r, c, v }]);
  }
  const groups = squarify([...sectors.entries()].map(([sector, rows]) => ({ item: { sector, rows }, value: rows.reduce((a, b) => a + b.v, 0) })), box);
  return groups.map(({ item, rect }) => {
    const inner = { x: rect.x + 1, y: rect.y + 14, w: Math.max(0, rect.w - 2), h: Math.max(0, rect.h - 15) };
    const tiles = squarify(item.rows.map((row) => ({ item: row, value: row.v })), inner).map(({ item: row, rect: t }) => ({
      symbol: row.r.symbol, sector: item.sector, matched: row.r.matched, unread: !!row.r.reason, change: row.c.change_pct,
      cap: row.c.market_cap, rect: t,
    }));
    return { sector: item.sector, rect, tiles, matched: tiles.filter((t) => t.matched).length };
  });
}

/** A tile's fill: the day's change on the up / down tokens (colour-blind safe), its strength by size (capped at 3 %). */
export function tileFill(change: number | null): { color: string; opacity: number } {
  if (change == null) return { color: "rgb(var(--surface-2))", opacity: 1 };
  const strength = Math.min(1, Math.abs(change) / 3);
  return { color: change >= 0 ? "rgb(var(--up))" : "rgb(var(--down))", opacity: 0.2 + 0.6 * strength };
}

/** An SVG path of closes in a w x h box (null with fewer than two). */
export function sparkPath(closes: { c: number }[], w: number, h: number): string | null {
  if (closes.length < 2) return null;
  const vals = closes.map((p) => p.c);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  const span = hi - lo || 1;
  return vals.map((v, i) => `${i ? "L" : "M"}${((i / (vals.length - 1)) * w).toFixed(1)} ${(h - ((v - lo) / span) * h).toFixed(1)}`).join("");
}

/** Roving focus in a tablist: the view an arrow / Home / End key moves to (null for any other key). */
export function nextTab<T>(ids: readonly T[], current: T, key: string): T | null {
  const i = ids.indexOf(current), n = ids.length;
  if (key === "ArrowRight" || key === "ArrowDown") return ids[(i + 1) % n];
  if (key === "ArrowLeft" || key === "ArrowUp") return ids[(i - 1 + n) % n];
  if (key === "Home") return ids[0];
  if (key === "End") return ids[n - 1];
  return null;
}

/** The grid: matched symbols first (up to GRID_MAX), in the table's order. */
export function gridSymbols(results: RunResult[]): RunResult[] {
  return results.filter((r) => r.matched).slice(0, GRID_MAX);
}
