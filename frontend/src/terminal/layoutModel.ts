/**
 * Trading Terminal T1 (docs/owner-orders/ATP_SESSION_TRADING_TERMINAL.md, review TT-16, TT-17): the workspace's layout
 * model - pure functions, unit-tested (terminal.test.tsx).
 *
 * - ATP's own 12-column grid (no grid library, TT-17): a panel sits at column `x`, row `y`, `w` columns wide and `h` rows
 *   high. Open panels never overlap and always fit inside the 12 columns; a move or resize that would break either is
 *   refused (the panel stays where it was).
 * - Three presets in code ("Option seller" - the default for a new user, owner decision T-D1 -, "Quick trade" and
 *   "Multi-chart"), never stored, so "Reset" needs no server.
 * - Up to MAX_LAYOUTS of the user's own layouts, stored on the server per user (prefs namespace `terminal`, backend
 *   app/me/prefs.py TerminalPrefs - the same limits): save, save as, rename, delete, switch, reset to the preset.
 */

export const COLUMNS = 12;
export const ROWS = 64;
export const MAX_HEIGHT = 32;
export const MAX_LAYOUTS = 10;
export const NAMESPACE = "terminal";
/** The same rule as the server (app/me/prefs.py TERMINAL_LAYOUT_NAME). */
export const NAME_RULE = /^[A-Za-z0-9][A-Za-z0-9 &()+\-/.,:']{0,39}$/;

export type PanelId = "chart-1" | "chart-2" | "chart-3" | "chart-4" | "chain" | "order" | "positions" | "orders" | "basket" | "bots" | "payoff";
export type PresetId = "option_seller" | "quick_trade" | "multi_chart";

export interface PanelPlace {
  id: PanelId;
  x: number;
  y: number;
  w: number;
  h: number;
  open: boolean;
  /** A chart's own symbol; null = the layout's underlying. */
  symbol?: string | null;
}

export interface Layout {
  name: string;
  preset: PresetId;
  /** The underlying the chain, the order panel and a chart without its own symbol follow; null = NIFTY. */
  underlying: string | null;
  panels: PanelPlace[];
  saved_at?: string | null;
}

export interface TerminalPrefs {
  layouts: Record<string, Layout>;
  /** A preset id or one of `layouts`' ids; null = the Option seller preset. */
  active: string | null;
}

export interface PanelInfo { label: string; minW: number; minH: number; w: number; h: number }

export const PANEL_IDS: readonly PanelId[] = ["chart-1", "chart-2", "chart-3", "chart-4", "chain", "order", "positions", "orders", "basket", "bots", "payoff"];
/** T2/T3 build them; until then hidden (R6, FN0 25 Oct): never in the Panels menu, never opened, a stored open one reads closed. */
export const HIDDEN_PANELS: ReadonlySet<PanelId> = new Set<PanelId>(["bots", "payoff"]);
/** The panels the "Panels" menu offers. */
export const CHOOSER_PANEL_IDS: readonly PanelId[] = PANEL_IDS.filter((id) => !HIDDEN_PANELS.has(id));

export const PANELS: Record<PanelId, PanelInfo> = {
  "chart-1": { label: "Chart", minW: 3, minH: 6, w: 6, h: 10 },
  "chart-2": { label: "Chart 2", minW: 3, minH: 6, w: 6, h: 8 },
  "chart-3": { label: "Chart 3", minW: 3, minH: 6, w: 6, h: 8 },
  "chart-4": { label: "Chart 4", minW: 3, minH: 6, w: 6, h: 8 },
  chain: { label: "Option chain", minW: 3, minH: 6, w: 3, h: 11 },
  order: { label: "Order", minW: 3, minH: 8, w: 4, h: 11 },
  // TERM-2 step 3: the one bottom panel - Positions · Orders · Alerts as tabs (TradingView's Account Manager pattern).
  positions: { label: "Positions & orders", minW: 4, minH: 5, w: 12, h: 8 },
  orders: { label: "Orders", minW: 3, minH: 4, w: 4, h: 7 },
  basket: { label: "Basket", minW: 3, minH: 4, w: 3, h: 7 },   // TERM-2 step 2: the one basket
  bots: { label: "Bots & signals", minW: 3, minH: 4, w: 4, h: 6 },
  payoff: { label: "Payoff", minW: 3, minH: 4, w: 4, h: 6 },
};

export const UNDERLYINGS: readonly string[] = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"];
export const DEFAULT_UNDERLYING = "NIFTY";

const p = (id: PanelId, x: number, y: number, w: number, h: number, open = true, symbol: string | null = null): PanelPlace =>
  ({ id, x, y, w, h, open, symbol });

export const PRESETS: Record<PresetId, { name: string; description: string; panels: PanelPlace[] }> = {
  // TERM-2 step 3 (owner 30 Oct): chart | chain | basket above the one bottom panel (Positions · Orders · Alerts). A price
  // on the chain opens the action sheet; Buy / Sell there open the one order ticket.
  option_seller: {
    name: "Option seller",
    description: "The chart, the option chain and your basket side by side; positions, orders and alerts below.",
    panels: [p("chart-1", 0, 0, 4, 12), p("chain", 4, 0, 5, 12), p("basket", 9, 0, 3, 12), p("positions", 0, 12, 12, 8),
      p("order", 0, 20, 4, 11, false), p("orders", 8, 20, 4, 7, false), p("bots", 0, 31, 6, 6, false), p("payoff", 6, 31, 6, 6, false)],
  },
  quick_trade: {
    name: "Quick trade",
    description: "The option chain with the order panel beside it; positions, orders and alerts below.",
    panels: [p("chain", 0, 0, 7, 12), p("order", 7, 0, 5, 12), p("positions", 0, 12, 12, 8),
      p("chart-1", 0, 20, 6, 10, false), p("basket", 6, 20, 3, 7, false), p("orders", 9, 20, 3, 7, false)],
  },
  multi_chart: {
    name: "Multi-chart",
    description: "Four charts at once, with your positions below.",
    panels: [p("chart-1", 0, 0, 6, 8, true, "NIFTY 50"), p("chart-2", 6, 0, 6, 8, true, "NIFTY BANK"),
      p("chart-3", 0, 8, 6, 8, true, "NIFTY FIN SERVICE"), p("chart-4", 6, 8, 6, 8, true, "NIFTY IT"),
      p("positions", 0, 16, 12, 5), p("order", 8, 21, 4, 11, false)],
  },
};

export const PRESET_IDS: readonly PresetId[] = ["option_seller", "quick_trade", "multi_chart"];
export const DEFAULT_PRESET: PresetId = "option_seller";

export const isPreset = (key: string | null | undefined): key is PresetId => !!key && (PRESET_IDS as readonly string[]).includes(key);

/** A preset as a working layout (a fresh copy every time). */
export function presetLayout(id: PresetId): Layout {
  return { name: PRESETS[id].name, preset: id, underlying: null, panels: PRESETS[id].panels.map((x) => ({ ...x })) };
}

export function overlaps(a: PanelPlace, b: PanelPlace): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/** True when the panel fits inside the grid at its own size limits. */
export function fits(place: PanelPlace): boolean {
  const info = PANELS[place.id];
  return Number.isInteger(place.x) && Number.isInteger(place.y) && place.x >= 0 && place.y >= 0 && place.w >= 1 && place.h >= 1
    && place.x + place.w <= COLUMNS && place.y + place.h <= ROWS && place.h <= MAX_HEIGHT && !!info;
}

/** What is wrong with a set of panels (null = nothing): the rules the server checks too. */
export function layoutProblem(panels: readonly PanelPlace[]): string | null {
  const ids = panels.map((x) => x.id);
  if (new Set(ids).size !== ids.length) return "A panel appears twice.";
  if (panels.some((x) => !fits(x))) return "A panel does not fit inside the grid.";
  const shown = panels.filter((x) => x.open);
  if (!shown.length) return "Open at least one panel.";
  for (let i = 0; i < shown.length; i += 1) {
    for (let j = i + 1; j < shown.length; j += 1) if (overlaps(shown[i], shown[j])) return "Two panels overlap.";
  }
  return null;
}

function placeAt(panels: readonly PanelPlace[], id: PanelId, change: (x: PanelPlace) => PanelPlace): PanelPlace[] | null {
  const next = panels.map((x) => (x.id === id ? change(x) : x));
  return layoutProblem(next) === null ? next : null;
}

/** Move a panel to column `x`, row `y` (kept inside the grid); null when it would cover another open panel. */
export function movePanel(panels: readonly PanelPlace[], id: PanelId, x: number, y: number): PanelPlace[] | null {
  return placeAt(panels, id, (q) => ({
    ...q, x: Math.max(0, Math.min(COLUMNS - q.w, Math.round(x))), y: Math.max(0, Math.min(ROWS - q.h, Math.round(y))),
  }));
}

/** Resize a panel to `w` x `h` (kept within its own minimum and the grid); null when it would cover another open panel. */
export function resizePanel(panels: readonly PanelPlace[], id: PanelId, w: number, h: number): PanelPlace[] | null {
  return placeAt(panels, id, (q) => {
    const info = PANELS[q.id];
    return {
      ...q, w: Math.max(info.minW, Math.min(COLUMNS - q.x, Math.round(w))), h: Math.max(info.minH, Math.min(MAX_HEIGHT, ROWS - q.y, Math.round(h))),
    };
  });
}

/** The first free place (top to bottom, left to right) for a `w` x `h` panel among the open ones; null when none. */
export function firstFree(panels: readonly PanelPlace[], w: number, h: number, except?: PanelId): { x: number; y: number } | null {
  const shown = panels.filter((x) => x.open && x.id !== except);
  for (let y = 0; y + h <= ROWS; y += 1) {
    for (let x = 0; x + w <= COLUMNS; x += 1) {
      const probe: PanelPlace = { id: except ?? "chart-1", x, y, w, h, open: true };
      if (!shown.some((s) => overlaps(s, probe))) return { x, y };
    }
  }
  return null;
}

/** Open a panel: at its last place when that is free, else the first free place at its usual size (then its minimum). */
export function openPanel(panels: readonly PanelPlace[], id: PanelId): PanelPlace[] {
  if (HIDDEN_PANELS.has(id)) return [...panels];
  const info = PANELS[id];
  const known = panels.find((x) => x.id === id);
  if (known?.open) return [...panels];
  const others = panels.filter((x) => x.id !== id);
  if (known && layoutProblem([...others, { ...known, open: true }]) === null) return panels.map((x) => (x.id === id ? { ...x, open: true } : x));
  for (const [w, h] of [[info.w, info.h], [info.minW, info.minH]]) {
    const at = firstFree(panels, w, h, id);
    if (at) {
      const place: PanelPlace = { ...(known ?? { id, symbol: null }), id, x: at.x, y: at.y, w, h, open: true };
      return known ? panels.map((x) => (x.id === id ? place : x)) : [...panels, place];
    }
  }
  return [...panels];
}

/** Close a panel (it keeps its place for when it opens again). The last open panel stays open. */
export function closePanel(panels: readonly PanelPlace[], id: PanelId): PanelPlace[] {
  if (panels.filter((x) => x.open).length <= 1) return [...panels];
  return panels.map((x) => (x.id === id ? { ...x, open: false } : x));
}

/** The panels in reading order (top to bottom, then left to right) - the keyboard and screen-reader order. */
export function readingOrder(panels: readonly PanelPlace[]): PanelPlace[] {
  return panels.filter((x) => x.open).sort((a, b) => a.y - b.y || a.x - b.x);
}

/** How many rows the open panels use (the grid's height). */
export function usedRows(panels: readonly PanelPlace[]): number {
  return Math.max(1, ...panels.filter((x) => x.open).map((x) => x.y + x.h));
}

// --- M58 (T4): the lines between panels, the Panels dialog's sizes, maximise ----------------------------------------------

/** "x": a vertical line between columns; "y": a horizontal line between rows. */
export type Axis = "x" | "y";

/**
 * T4-1: a shared edge between adjacent open panels - the gutter the user drags. `at` is the grid line (a column for "x",
 * a row for "y"); `from` / `to` is where it runs along the other axis; `before` are the panels left of / above the line,
 * `after` those right of / below it. Dragging the line grows one side and shrinks the other by the same amount.
 */
export interface Gutter { axis: Axis; at: number; from: number; to: number; before: PanelId[]; after: PanelId[] }

/** T4-2: why a size or a move could not (fully) be applied - shown inline, never a silent refusal. */
export interface SizeLimit { kind: "min" | "max" | "edge" | "blocked"; text: string; panel?: PanelId }

export interface SizeResult { panels: PanelPlace[]; moved: number; limit: SizeLimit | null }

const startOf = (q: PanelPlace, axis: Axis) => (axis === "x" ? q.x : q.y);
const sizeOf = (q: PanelPlace, axis: Axis) => (axis === "x" ? q.w : q.h);
const crossOf = (q: PanelPlace, axis: Axis): [number, number] => (axis === "x" ? [q.y, q.y + q.h] : [q.x, q.x + q.w]);
const meets = (a: PanelPlace, b: PanelPlace, axis: Axis) => {
  const [a0, a1] = crossOf(a, axis), [b0, b1] = crossOf(b, axis);
  return a0 < b1 && b0 < a1;
};

/** The gutters of a layout: every grid line with open panels touching it from both sides, one gutter per connected run. */
export function gutters(panels: readonly PanelPlace[]): Gutter[] {
  const shown = panels.filter((q) => q.open);
  const out: Gutter[] = [];
  for (const axis of ["x", "y"] as const) {
    const lines = [...new Set(shown.map((q) => startOf(q, axis) + sizeOf(q, axis)))].sort((a, b) => a - b);
    for (const at of lines) {
      const before = shown.filter((q) => startOf(q, axis) + sizeOf(q, axis) === at);
      const after = shown.filter((q) => startOf(q, axis) === at);
      const used = new Set<PanelId>();
      for (const seed of before) {
        if (used.has(seed.id)) continue;
        const b: PanelPlace[] = [seed], a: PanelPlace[] = [];
        used.add(seed.id);
        for (let grew = true; grew;) {
          grew = false;
          for (const q of after) if (!used.has(q.id) && b.some((x) => meets(x, q, axis))) { a.push(q); used.add(q.id); grew = true; }
          for (const q of before) if (!used.has(q.id) && a.some((x) => meets(x, q, axis))) { b.push(q); used.add(q.id); grew = true; }
        }
        if (!a.length) continue;
        const lo = (l: PanelPlace[]) => Math.min(...l.map((q) => crossOf(q, axis)[0]));
        const hi = (l: PanelPlace[]) => Math.max(...l.map((q) => crossOf(q, axis)[1]));
        const order = (l: PanelPlace[]) => [...l].sort((m, n) => crossOf(m, axis)[0] - crossOf(n, axis)[0]).map((q) => q.id);
        out.push({ axis, at, from: Math.max(lo(b), lo(a)), to: Math.min(hi(b), hi(a)), before: order(b), after: order(a) });
      }
    }
  }
  return out;
}

/** The words for a gutter (its aria-label), e.g. "Resize Chart and Option chain". */
export function gutterLabel(g: Gutter, name: (id: PanelId) => string = (id) => PANELS[id].label): string {
  const names = [...g.before, ...g.after].map(name);
  return `Resize ${names.length > 1 ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}` : names[0]}`;
}

const minText = (id: PanelId, axis: Axis) => {
  const info = PANELS[id];
  return axis === "x" ? `${info.label} cannot be narrower than ${info.minW} columns.` : `${info.label} cannot be shorter than ${info.minH} rows.`;
};
const maxText = (id: PanelId) => `${PANELS[id].label} cannot be taller than ${MAX_HEIGHT} rows.`;
const EDGE_TEXT = "This is the edge of the grid.";
const BLOCKED_TEXT = "Another panel is in the way.";

/** Why `next` is not a layout the grid takes, in the words the user sees (null = it is fine). */
function limitOf(next: readonly PanelPlace[], changedIds: readonly PanelId[], axis: Axis): SizeLimit | null {
  for (const id of changedIds) {
    const q = next.find((x) => x.id === id);
    if (!q) continue;
    const info = PANELS[id];
    if (axis === "x" && q.w < info.minW) return { kind: "min", panel: id, text: minText(id, "x") };
    if (axis === "y" && q.h < info.minH) return { kind: "min", panel: id, text: minText(id, "y") };
    if (q.h > MAX_HEIGHT) return { kind: "max", panel: id, text: maxText(id) };
    if (q.x < 0 || q.y < 0 || q.x + q.w > COLUMNS || q.y + q.h > ROWS) return { kind: "edge", panel: id, text: EDGE_TEXT };
  }
  const problem = layoutProblem(next);
  if (problem === "Two panels overlap.") return { kind: "blocked", text: BLOCKED_TEXT };
  return problem ? { kind: "edge", text: EDGE_TEXT } : null;
}

/**
 * T4-1: drag a gutter by `delta` columns (axis "x") or rows (axis "y"): the panels before the line grow by `delta` and
 * the ones after it shrink by the same (a negative `delta` the other way round). When the whole step does not fit (a
 * panel at its minimum, the tallest height, another panel in the way), the largest step that fits is applied and
 * `limit` says why it stopped (T4-2) - never refused silently.
 */
export function resizeAcrossEdge(panels: readonly PanelPlace[], g: Gutter, delta: number): SizeResult {
  const d = Math.round(delta);
  const ids = [...g.before, ...g.after];
  const apply = (k: number): PanelPlace[] => panels.map((q) => {
    if (g.before.includes(q.id)) return g.axis === "x" ? { ...q, w: q.w + k } : { ...q, h: q.h + k };
    if (g.after.includes(q.id)) return g.axis === "x" ? { ...q, x: q.x + k, w: q.w - k } : { ...q, y: q.y + k, h: q.h - k };
    return q;
  });
  if (d === 0) return { panels: [...panels], moved: 0, limit: null };
  const sign = Math.sign(d);
  for (let k = d; k !== 0; k -= sign) {
    const next = apply(k);
    if (limitOf(next, ids, g.axis) === null) return { panels: next, moved: k, limit: k === d ? null : limitOf(apply(k + sign), ids, g.axis) };
  }
  return { panels: [...panels], moved: 0, limit: limitOf(apply(sign), ids, g.axis) };
}

/**
 * T4-2: why a move to column `a`, row `b` (kind "move") or a resize to `a` x `b` (kind "resize", by the corner or Shift
 * and the arrow keys) is not applied as asked; null when it is.
 */
export function placeLimit(panels: readonly PanelPlace[], id: PanelId, kind: "move" | "resize", a: number, b: number): SizeLimit | null {
  const q = panels.find((x) => x.id === id);
  if (!q) return null;
  const info = PANELS[id];
  if (kind === "resize") {
    if (a < info.minW) return { kind: "min", panel: id, text: minText(id, "x") };
    if (b < info.minH) return { kind: "min", panel: id, text: minText(id, "y") };
    if (b > MAX_HEIGHT) return { kind: "max", panel: id, text: maxText(id) };
    if (q.x + a > COLUMNS || q.y + b > ROWS) return { kind: "edge", panel: id, text: EDGE_TEXT };
    return resizePanel(panels, id, a, b) ? null : { kind: "blocked", panel: id, text: `${BLOCKED_TEXT} Drag the line between two panels to share the space.` };
  }
  if (a < 0 || b < 0 || a + q.w > COLUMNS || b + q.h > ROWS) return { kind: "edge", panel: id, text: EDGE_TEXT };
  return movePanel(panels, id, a, b) ? null : { kind: "blocked", panel: id, text: BLOCKED_TEXT };
}

/**
 * T4-3: the Panels dialog's steppers - set a panel's width (`dim` "w", columns) or height ("h", rows). Free space is
 * used first; else the panel's right / bottom line moves (its neighbours there shrink), then its left / top line.
 */
export function sizePanel(panels: readonly PanelPlace[], id: PanelId, dim: "w" | "h", value: number): SizeResult {
  const q = panels.find((x) => x.id === id && x.open);
  if (!q) return { panels: [...panels], moved: 0, limit: null };
  const info = PANELS[id];
  const axis: Axis = dim === "w" ? "x" : "y";
  const want = Math.round(value);
  const delta = want - q[dim];
  if (delta === 0) return { panels: [...panels], moved: 0, limit: null };
  if (want < (dim === "w" ? info.minW : info.minH)) return { panels: [...panels], moved: 0, limit: { kind: "min", panel: id, text: minText(id, axis) } };
  if (dim === "h" && want > MAX_HEIGHT) return { panels: [...panels], moved: 0, limit: { kind: "max", panel: id, text: maxText(id) } };
  const free = resizePanel(panels, id, dim === "w" ? want : q.w, dim === "h" ? want : q.h);
  if (free && free.find((x) => x.id === id)?.[dim] === want) return { panels: free, moved: delta, limit: null };
  let now: SizeResult = { panels: [...panels], moved: 0, limit: null };
  const far = gutters(panels).find((g) => g.axis === axis && g.before.includes(id));
  if (far) now = resizeAcrossEdge(panels, far, delta);
  if (now.moved !== delta) {
    const near = gutters(now.panels).find((g) => g.axis === axis && g.after.includes(id));
    if (near) {
      const r = resizeAcrossEdge(now.panels, near, -(delta - now.moved));
      now = { panels: r.panels, moved: now.moved - r.moved, limit: r.limit };
    }
  }
  if (now.moved !== delta && !now.limit) {
    const edge = (dim === "w" && q.x + want > COLUMNS) || (dim === "h" && q.y + want > ROWS);
    now = { ...now, limit: edge ? { kind: "edge", panel: id, text: EDGE_TEXT } : { kind: "blocked", panel: id, text: BLOCKED_TEXT } };
  }
  return now;
}

/** Scale the grid lines by `fx` / `fy`: panels that touched still touch and none overlap (rounding keeps the order). */
function scaleLines(panels: readonly PanelPlace[], fx: number, fy: number): PanelPlace[] {
  return panels.map((q) => {
    if (!q.open) return q;
    const x = Math.round(q.x * fx), y = Math.round(q.y * fy);
    return { ...q, x, y, w: Math.round((q.x + q.w) * fx) - x, h: Math.round((q.y + q.h) * fy) - y };
  });
}

const tooSmall = (next: readonly PanelPlace[]) => next.some((q) => q.open && (q.w < PANELS[q.id].minW || q.h < PANELS[q.id].minH));

/**
 * T4-3 "Fit to screen": the open panels fill the 12 columns and `rows` rows (the height the screen shows); every panel
 * keeps its place among the others. When the screen is too short for every panel at its smallest, only the width fits.
 */
export function fitToScreen(panels: readonly PanelPlace[], rows: number): SizeResult {
  const shown = panels.filter((q) => q.open);
  if (!shown.length) return { panels: [...panels], moved: 0, limit: null };
  const right = Math.max(...shown.map((q) => q.x + q.w));
  const fx = COLUMNS / right, fy = Math.max(1, Math.round(rows)) / usedRows(panels);
  const both = scaleLines(panels, fx, fy);
  if (!tooSmall(both) && layoutProblem(both) === null) return { panels: both, moved: 1, limit: null };
  const wide = scaleLines(panels, fx, 1);
  const limit: SizeLimit = { kind: "min", text: "The screen is too short for every open panel at its smallest height." };
  if (!tooSmall(wide) && layoutProblem(wide) === null) return { panels: wide, moved: 1, limit };
  return { panels: [...panels], moved: 0, limit };
}

/**
 * T4-3 "Equal widths": every row of side-by-side panels (panels whose rows overlap, none above another) shares the 12
 * columns equally (the first ones take the spare columns). A row that cannot be shared this way keeps its widths.
 */
export function equalWidths(panels: readonly PanelPlace[]): SizeResult {
  const shown = readingOrder(panels);
  const used = new Set<PanelId>();
  let next = [...panels];
  let skipped = false;
  for (const seed of shown) {
    if (used.has(seed.id)) continue;
    const band: PanelPlace[] = [seed];
    used.add(seed.id);
    for (let grew = true; grew;) {
      grew = false;
      for (const q of shown) if (!used.has(q.id) && band.some((b) => meets(b, q, "x"))) { band.push(q); used.add(q.id); grew = true; }
    }
    const row = band.sort((a, b) => a.x - b.x);
    const strip = row.every((q, i) => i === 0 || row[i - 1].x + row[i - 1].w <= q.x);
    const base = Math.floor(COLUMNS / row.length), spare = COLUMNS - base * row.length;
    if (!strip || row.some((q, i) => base + (i < spare ? 1 : 0) < PANELS[q.id].minW)) { if (row.length > 1) skipped = true; continue; }
    let x = 0;
    const placed = new Map(row.map((q, i) => {
      const w = base + (i < spare ? 1 : 0);
      const at = { ...q, x, w };
      x += w;
      return [q.id, at] as const;
    }));
    next = next.map((q) => placed.get(q.id) ?? q);
  }
  if (layoutProblem(next) !== null) return { panels: [...panels], moved: 0, limit: { kind: "blocked", text: BLOCKED_TEXT } };
  const moved = next.some((q, i) => q.x !== panels[i].x || q.w !== panels[i].w) ? 1 : 0;
  return { panels: next, moved, limit: skipped ? { kind: "blocked", text: "Panels stacked above one another keep their widths." } : null };
}

/**
 * T4-4: double-click a gutter - the panels on it go back to their sizes in the ready layout (`preset`). When the ready
 * layout does not have one of them, the line goes back to where the ready layout has it.
 */
export function resetEdge(panels: readonly PanelPlace[], g: Gutter, preset: PresetId): SizeResult {
  const base = PRESETS[preset].panels;
  const ids = [...g.before, ...g.after];
  const b0 = base.find((x) => x.id === g.before[0]) ?? null;
  const a0 = base.find((x) => x.id === g.after[0]) ?? null;
  const line = b0 ? startOf(b0, g.axis) + sizeOf(b0, g.axis) : a0 ? startOf(a0, g.axis) : null;
  if (line === null) return { panels: [...panels], moved: 0, limit: { kind: "blocked", text: "These panels have no size in the ready layout to go back to." } };
  const literal = panels.map((q) => {
    const b = ids.includes(q.id) ? base.find((x) => x.id === q.id) : undefined;
    if (!b) return q;
    return g.axis === "x" ? { ...q, x: b.x, w: b.w } : { ...q, y: b.y, h: b.h };
  });
  if (ids.every((id) => base.some((x) => x.id === id)) && !tooSmall(literal) && layoutProblem(literal) === null) {
    return { panels: literal, moved: line - g.at, limit: null };
  }
  return resizeAcrossEdge(panels, g, line - g.at);
}

/**
 * T4-4: double-click a panel's title - the view with that panel maximised: the other open panels collapse to their
 * titles in one strip on top, the panel takes the full width below. A view only (the layout is unchanged, so restoring
 * is showing the layout again).
 */
export function maximiseView(panels: readonly PanelPlace[], id: PanelId): PanelPlace[] {
  const target = panels.find((q) => q.id === id && q.open);
  if (!target) return [...panels];
  const others = readingOrder(panels).filter((q) => q.id !== id);
  const n = others.length;
  const per = n ? Math.max(1, Math.floor(COLUMNS / n)) : 0;
  const strip = new Map(others.map((q, i) => [q.id, { ...q, x: i * per, y: 0, w: i === n - 1 ? COLUMNS - i * per : per, h: 1 }] as const));
  const top = n ? 1 : 0;
  const h = Math.min(MAX_HEIGHT, Math.max(target.h, usedRows(panels) - top));
  return panels.map((q) => (q.id === id ? { ...q, x: 0, y: top, w: COLUMNS, h } : strip.get(q.id) ?? q));
}

/**
 * T4-5: the chain panel's column priority. At its ready-layout width (5 columns) and wider every column shows; one
 * column narrower IV goes first; at its smallest width OI change goes too (OI, volume and LTP always stay).
 */
export function chainHiddenColumns(w: number): ("iv" | "oi_change")[] {
  if (w >= 5) return [];
  return w <= PANELS.chain.minW ? ["iv", "oi_change"] : ["iv"];
}

// --- the user's own layouts ----------------------------------------------------------------------------------------------

export const EMPTY_PREFS: TerminalPrefs = { layouts: {}, active: null };

/** The layout in use: a saved one of his, else a preset (the Option seller for a new user, T-D1). */
export function activeKey(prefs: TerminalPrefs): string {
  if (prefs.active && (isPreset(prefs.active) || prefs.layouts[prefs.active])) return prefs.active;
  return DEFAULT_PRESET;
}

export function layoutFor(prefs: TerminalPrefs, key: string): Layout {
  const own = prefs.layouts[key];
  if (own) return { ...own, panels: own.panels.map((x) => ({ ...x })) };
  return presetLayout(isPreset(key) ? key : DEFAULT_PRESET);
}

/** What is wrong with a layout name (null = fine): the server's own rule, and not the name of another of his layouts. */
export function nameProblem(prefs: TerminalPrefs, name: string, exceptId?: string): string | null {
  const clean = name.trim();
  if (!clean) return "Give the layout a name.";
  if (!NAME_RULE.test(clean)) return "Use up to 40 letters, numbers, spaces and simple punctuation.";
  const taken = Object.entries(prefs.layouts).some(([id, l]) => id !== exceptId && l.name.trim().toLowerCase() === clean.toLowerCase());
  return taken ? "You already have a layout with this name." : null;
}

export function newLayoutId(rand: () => number = Math.random): string {
  let out = "";
  while (out.length < 8) out += Math.floor(rand() * 36).toString(36);
  return out.slice(0, 8);
}

type Change = { prefs: TerminalPrefs; id: string } | { error: string };

/** Save the working layout as a new one of his and switch to it; refused past MAX_LAYOUTS or with a bad name. */
export function saveAs(prefs: TerminalPrefs, working: Layout, name: string, id = newLayoutId(), now = new Date()): Change {
  if (Object.keys(prefs.layouts).length >= MAX_LAYOUTS) return { error: `You can keep ${MAX_LAYOUTS} layouts. Delete one to save another.` };
  const problem = nameProblem(prefs, name) ?? layoutProblem(working.panels);
  if (problem) return { error: problem };
  const layout: Layout = { ...working, name: name.trim(), panels: working.panels.map((x) => ({ ...x })), saved_at: now.toISOString() };
  return { prefs: { layouts: { ...prefs.layouts, [id]: layout }, active: id }, id };
}

/** Save the working layout over one of his own (its name stays). */
export function saveOver(prefs: TerminalPrefs, id: string, working: Layout, now = new Date()): Change {
  const own = prefs.layouts[id];
  if (!own) return { error: "This layout is no longer saved. Save it as a new one." };
  const problem = layoutProblem(working.panels);
  if (problem) return { error: problem };
  return { prefs: { ...prefs, layouts: { ...prefs.layouts, [id]: { ...working, name: own.name, panels: working.panels.map((x) => ({ ...x })), saved_at: now.toISOString() } } }, id };
}

export function rename(prefs: TerminalPrefs, id: string, name: string): Change {
  const own = prefs.layouts[id];
  if (!own) return { error: "This layout is no longer saved." };
  const problem = nameProblem(prefs, name, id);
  if (problem) return { error: problem };
  return { prefs: { ...prefs, layouts: { ...prefs.layouts, [id]: { ...own, name: name.trim() } } }, id };
}

/** Delete one of his layouts; the one in use falls back to its preset. */
export function remove(prefs: TerminalPrefs, id: string): TerminalPrefs {
  const own = prefs.layouts[id];
  if (!own) return prefs;
  const layouts = { ...prefs.layouts };
  delete layouts[id];
  return { layouts, active: prefs.active === id ? own.preset : prefs.active };
}

/** Switch to a preset or one of his layouts. */
export function switchTo(prefs: TerminalPrefs, key: string): TerminalPrefs {
  return isPreset(key) || prefs.layouts[key] ? { ...prefs, active: key } : prefs;
}

/** "Reset": the working layout back to its preset's panels (his underlying is kept). */
export function resetToPreset(working: Layout): Layout {
  return { ...presetLayout(working.preset), name: working.name, underlying: working.underlying };
}

/** True when the working layout differs from what is saved for the key (or from the preset). */
export function changed(prefs: TerminalPrefs, key: string, working: Layout): boolean {
  const base = layoutFor(prefs, key);
  const norm = (l: Layout) => JSON.stringify({ u: l.underlying ?? null, p: [...l.panels].sort((a, b) => a.id.localeCompare(b.id)).map((x) => [x.id, x.x, x.y, x.w, x.h, x.open, x.symbol ?? null]) });
  return norm(base) !== norm(working);
}

const int = (v: unknown, lo: number, hi: number): number | null => (typeof v === "number" && Number.isInteger(v) && v >= lo && v <= hi ? v : null);

function readPanel(raw: unknown): PanelPlace | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  if (!(PANEL_IDS as readonly unknown[]).includes(r.id)) return null;
  const x = int(r.x, 0, COLUMNS - 1), y = int(r.y, 0, ROWS - 1), w = int(r.w, 1, COLUMNS), h = int(r.h, 1, MAX_HEIGHT);
  if (x === null || y === null || w === null || h === null) return null;
  const id = r.id as PanelId;
  return { id, x, y, w, h, open: r.open !== false && !HIDDEN_PANELS.has(id), symbol: typeof r.symbol === "string" ? r.symbol : null };
}

/** The namespace as the server sent it, read defensively (anything that does not read is left out). */
export function readPrefs(raw: unknown): TerminalPrefs {
  if (!raw || typeof raw !== "object") return { ...EMPTY_PREFS, layouts: {} };
  const r = raw as Record<string, unknown>;
  const layouts: Record<string, Layout> = {};
  for (const [id, value] of Object.entries((r.layouts as Record<string, unknown>) ?? {})) {
    if (!value || typeof value !== "object") continue;
    const l = value as Record<string, unknown>;
    const panels = Array.isArray(l.panels) ? l.panels.map(readPanel).filter((x): x is PanelPlace => x !== null) : [];
    if (typeof l.name !== "string" || !panels.length || layoutProblem(panels) !== null) continue;
    layouts[id] = { name: l.name, preset: isPreset(l.preset as string) ? (l.preset as PresetId) : DEFAULT_PRESET,
      underlying: typeof l.underlying === "string" ? l.underlying : null, panels, saved_at: typeof l.saved_at === "string" ? l.saved_at : null };
  }
  return { layouts, active: typeof r.active === "string" ? r.active : null };
}

/** The body the server takes (unknown keys never sent). */
export function forSave(prefs: TerminalPrefs): TerminalPrefs {
  const layouts: Record<string, Layout> = {};
  for (const [id, l] of Object.entries(prefs.layouts)) {
    layouts[id] = {
      name: l.name, preset: l.preset, underlying: l.underlying ?? null, saved_at: l.saved_at ?? null,
      panels: l.panels.map((x) => ({ id: x.id, x: x.x, y: x.y, w: x.w, h: x.h, open: x.open, symbol: x.id.startsWith("chart-") ? x.symbol ?? null : null })),
    };
  }
  return { layouts, active: prefs.active };
}

// --- the phone ---------------------------------------------------------------------------------------------------------

/** Phone (360 / 390): the panels as tabs, one at a time (T1). "bots" stays a type but no tab until T2/T3 (HIDDEN_PANELS). */
export type PhoneTab = "chart" | "chain" | "basket" | "order" | "positions" | "orders" | "bots";
export const PHONE_TABS: readonly { key: PhoneTab; label: string }[] = [
  // TERM-2 step 3: Chart · Chain · Basket · Positions · Orders; a price opens the order ticket as a bottom sheet.
  { key: "chart", label: "Chart" }, { key: "chain", label: "Chain" }, { key: "basket", label: "Basket" },
  { key: "positions", label: "Positions" }, { key: "orders", label: "Orders" },
];
