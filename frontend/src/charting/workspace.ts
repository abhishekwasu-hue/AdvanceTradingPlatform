/**
 * CH5: the multi-chart workspace model (`layout/1`), engine-neutral and free of React and chart code, so every rule is
 * a plain function with a test. The backend (`app/charts/layouts.py`) stores the same shape per user.
 *
 * - A grid of 1, 2, 4, 6 or 8 charts; each cell has its symbol, exchange, timeframe, chart type and an optional link
 *   group (A-D).
 * - A link group says which changes travel between its charts: the symbol, the interval (timeframe) and the crosshair.
 *   Changing one chart of a group changes the others that the group links; a chart joining a group takes the group's
 *   linked symbol / interval.
 * - Nothing here places an order.
 */
import { ApiError, request } from "../api/client";

export const SCHEMA = "layout/1";
export const GRIDS = [1, 2, 4, 6, 8] as const;
export type Grid = (typeof GRIDS)[number];
export const GROUPS = ["A", "B", "C", "D"] as const;
export type Group = (typeof GROUPS)[number];
export const TIMEFRAMES = ["1min", "3min", "5min", "15min", "30min", "60min", "day"] as const;
export type Timeframe = (typeof TIMEFRAMES)[number];
export const CHART_TYPES = ["candles", "hollow", "heikin_ashi", "renko", "range", "line", "area"] as const;
export type ChartType = (typeof CHART_TYPES)[number];
export const EXCHANGES = ["NSE", "BSE", "NFO", "BFO", "MCX", "CDS", "CRYPTO", "NYSE", "NASDAQ"] as const;
const SYMBOL_RE = /^[A-Za-z0-9 &._:-]{1,40}$/;

export interface CellV1 { symbol: string; exchange: string; timeframe: Timeframe; chart_type: ChartType; group: Group | null }
export interface GroupSync { symbol: boolean; interval: boolean; crosshair: boolean }
export interface LayoutV1 { schema: typeof SCHEMA; grid: Grid; cells: CellV1[]; groups: Partial<Record<Group, GroupSync>>; active: number }

/** A new group links the symbol and the crosshair; the interval only when asked (charts of one symbol on 5m and 1h). */
export const DEFAULT_SYNC: GroupSync = { symbol: true, interval: false, crosshair: true };
/** Group colours for the cell badges (the hue only tells groups apart; the letter is always shown too). */
export const GROUP_COLORS: Record<Group, string> = { A: "#38bdf8", B: "#f59e0b", C: "#a78bfa", D: "#22c55e" };

/** Columns x rows of each grid: 2 side by side, 4 as 2x2, 6 as 3x2, 8 as 4x2. */
export function gridShape(grid: Grid): { cols: number; rows: number } {
  switch (grid) {
    case 1: return { cols: 1, rows: 1 };
    case 2: return { cols: 2, rows: 1 };
    case 4: return { cols: 2, rows: 2 };
    case 6: return { cols: 3, rows: 2 };
    case 8: return { cols: 4, rows: 2 };
  }
}

export function cell(symbol: string, exchange = "NSE", timeframe: Timeframe = "5min"): CellV1 {
  return { symbol: symbol.trim().toUpperCase(), exchange: exchange.toUpperCase(), timeframe, chart_type: "candles", group: null };
}

export function defaultLayout(symbol = "NIFTY 50", exchange = "NSE", timeframe: Timeframe = "5min"): LayoutV1 {
  return { schema: SCHEMA, grid: 1, cells: [cell(symbol, exchange, timeframe)], groups: {}, active: 0 };
}

export function syncOf(layout: LayoutV1, group: Group | null): GroupSync | null {
  return group ? layout.groups[group] ?? DEFAULT_SYNC : null;
}

/** The other charts of `index`'s group that a change of `what` reaches (never the chart itself). */
export function linkedPeers(layout: LayoutV1, index: number, what: keyof GroupSync): number[] {
  const group = layout.cells[index]?.group ?? null;
  const sync = syncOf(layout, group);
  if (!group || !sync?.[what]) return [];
  return layout.cells.flatMap((c, i) => (i !== index && c.group === group ? [i] : []));
}

function withCells(layout: LayoutV1, change: (cells: CellV1[]) => void): LayoutV1 {
  const cells = layout.cells.map((c) => ({ ...c }));
  change(cells);
  return { ...layout, cells };
}

/** A new grid size: the first cells are kept; new cells show the active chart's symbol and timeframe, unlinked. */
export function setGrid(layout: LayoutV1, grid: Grid): LayoutV1 {
  const base = layout.cells[layout.active] ?? layout.cells[0];
  const cells = Array.from({ length: grid }, (_, i) => (layout.cells[i] ? { ...layout.cells[i] } : { ...base, chart_type: "candles" as ChartType, group: null }));
  return { ...layout, grid, cells, active: Math.min(layout.active, grid - 1) };
}

/** One chart's symbol (and exchange); the charts its group links by symbol follow. */
export function setSymbol(layout: LayoutV1, index: number, symbol: string, exchange?: string): LayoutV1 {
  const sym = symbol.trim().toUpperCase();
  if (!SYMBOL_RE.test(sym)) return layout;
  const exch = (exchange ?? layout.cells[index]?.exchange ?? "NSE").toUpperCase();
  return withCells(layout, (cells) => {
    for (const i of [index, ...linkedPeers(layout, index, "symbol")]) cells[i] = { ...cells[i], symbol: sym, exchange: exch };
  });
}

/** One chart's timeframe; the charts its group links by interval follow. */
export function setTimeframe(layout: LayoutV1, index: number, timeframe: Timeframe): LayoutV1 {
  return withCells(layout, (cells) => {
    for (const i of [index, ...linkedPeers(layout, index, "interval")]) cells[i] = { ...cells[i], timeframe };
  });
}

/** The chart type is per chart (a Renko next to its candles is the point), never linked. */
export function setChartType(layout: LayoutV1, index: number, chartType: ChartType): LayoutV1 {
  return withCells(layout, (cells) => { cells[index] = { ...cells[index], chart_type: chartType }; });
}

/** Moves a chart into a group (or out, with null). Joining takes the group's linked symbol / interval from a chart
 * already in it, so a group always shows one symbol (and one interval when linked). */
export function setGroup(layout: LayoutV1, index: number, group: Group | null): LayoutV1 {
  const groups = group && !layout.groups[group] ? { ...layout.groups, [group]: { ...DEFAULT_SYNC } } : layout.groups;
  const next = { ...withCells(layout, (cells) => { cells[index] = { ...cells[index], group }; }), groups };
  if (!group) return next;
  const member = next.cells.find((c, i) => i !== index && c.group === group);
  if (!member) return next;
  const sync = syncOf(next, group) as GroupSync;
  return withCells(next, (cells) => {
    if (sync.symbol) cells[index] = { ...cells[index], symbol: member.symbol, exchange: member.exchange };
    if (sync.interval) cells[index] = { ...cells[index], timeframe: member.timeframe };
  });
}

/** none -> A -> B -> C -> D -> none */
export function nextGroup(group: Group | null): Group | null {
  if (group === null) return GROUPS[0];
  const i = GROUPS.indexOf(group);
  return i === GROUPS.length - 1 ? null : GROUPS[i + 1];
}

/** Turns one kind of link of a group on or off. Turning the symbol (or interval) link on aligns the group to its
 * first chart, so the linked charts agree from then on. */
export function setGroupSync(layout: LayoutV1, group: Group, patch: Partial<GroupSync>): LayoutV1 {
  const sync = { ...(layout.groups[group] ?? DEFAULT_SYNC), ...patch };
  let next: LayoutV1 = { ...layout, groups: { ...layout.groups, [group]: sync } };
  const first = next.cells.findIndex((c) => c.group === group);
  if (first < 0) return next;
  if (patch.symbol) next = setSymbol(next, first, next.cells[first].symbol, next.cells[first].exchange);
  if (patch.interval) next = setTimeframe(next, first, next.cells[first].timeframe);
  return next;
}

/** The active chart moves by `delta`, wrapping round the grid. */
export function moveActive(layout: LayoutV1, delta: number): LayoutV1 {
  const n = layout.cells.length;
  return { ...layout, active: (((layout.active + delta) % n) + n) % n };
}

export function setActive(layout: LayoutV1, index: number): LayoutV1 {
  return index >= 0 && index < layout.cells.length && index !== layout.active ? { ...layout, active: index } : layout;
}

/** The next / previous timeframe in TIMEFRAMES order, stopping at either end. */
export function stepTimeframe(timeframe: Timeframe, delta: number): Timeframe {
  const i = TIMEFRAMES.indexOf(timeframe);
  return TIMEFRAMES[Math.max(0, Math.min(TIMEFRAMES.length - 1, (i < 0 ? 2 : i) + delta))];
}

/** The backend's rules, checked before a layout is used (a stored draft) or sent; null when it is valid. */
export function layoutProblem(value: unknown): string | null {
  const l = value as Partial<LayoutV1> | null;
  if (!l || typeof l !== "object") return "not a layout";
  if (l.schema !== SCHEMA) return `schema must be ${SCHEMA}`;
  if (!GRIDS.includes(l.grid as Grid)) return "grid must be 1, 2, 4, 6 or 8";
  if (!Array.isArray(l.cells) || l.cells.length !== l.grid) return `a ${l.grid}-chart grid needs ${l.grid} cells`;
  for (const c of l.cells) {
    if (!c || typeof c.symbol !== "string" || !SYMBOL_RE.test(c.symbol)) return "a chart has no valid symbol";
    if (!EXCHANGES.includes(c.exchange as (typeof EXCHANGES)[number])) return `unknown exchange ${String(c.exchange)}`;
    if (!TIMEFRAMES.includes(c.timeframe)) return `unknown timeframe ${String(c.timeframe)}`;
    if (!CHART_TYPES.includes(c.chart_type)) return `unknown chart type ${String(c.chart_type)}`;
    if (c.group !== null && !GROUPS.includes(c.group)) return `unknown link group ${String(c.group)}`;
  }
  if (!l.groups || typeof l.groups !== "object") return "groups missing";
  for (const g of Object.keys(l.groups)) if (!GROUPS.includes(g as Group)) return `unknown link group ${g}`;
  if (typeof l.active !== "number" || l.active < 0 || l.active >= (l.grid as number)) return "the active chart must be one of the grid's";
  return null;
}

// --- storage ---------------------------------------------------------------------------------------------------------
export interface StoredLayout { id: number; name: string; layout: LayoutV1; version: number; updated_at: string | null }

/** 409: the layout changed in another tab since it was loaded (`current` is the stored one); 404: deleted elsewhere. */
export class LayoutConflict extends Error {
  readonly current: StoredLayout | null;

  constructor(current: StoredLayout | null) {
    super("This layout changed in another window. Load it again or save under a new name.");
    this.name = "LayoutConflict";
    this.current = current;
  }
}

function conflictFrom(error: unknown): never {
  if (error instanceof ApiError && error.status === 404) throw new LayoutConflict(null);
  if (error instanceof ApiError && error.status === 409) {
    let current: StoredLayout | null = null;
    try {
      current = (JSON.parse(error.body) as { detail?: { current?: StoredLayout } }).detail?.current ?? null;
    } catch {
      current = null;
    }
    if (current) throw new LayoutConflict(current);
  }
  throw error;
}

const base = "/charts/layouts";

export const layoutsApi = {
  list: () => request<StoredLayout[]>(base),
  create: (name: string, layout: LayoutV1) => request<StoredLayout>(base, { method: "POST", body: JSON.stringify({ name, layout }) }),
  update: (stored: StoredLayout, layout: LayoutV1, name?: string) =>
    request<StoredLayout>(`${base}/${stored.id}`, { method: "PUT", body: JSON.stringify({ version: stored.version, layout, ...(name ? { name } : {}) }) })
      .catch(conflictFrom),
  remove: (stored: StoredLayout) => request<{ deleted: number }>(`${base}/${stored.id}?version=${stored.version}`, { method: "DELETE" }).catch(conflictFrom),
};
