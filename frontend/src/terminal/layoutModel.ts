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
  positions: { label: "Positions", minW: 3, minH: 4, w: 5, h: 7 },
  orders: { label: "Orders", minW: 3, minH: 4, w: 4, h: 7 },
  basket: { label: "Spread builder", minW: 3, minH: 4, w: 3, h: 7 },
  bots: { label: "Bots & signals", minW: 3, minH: 4, w: 4, h: 6 },
  payoff: { label: "Payoff", minW: 3, minH: 4, w: 4, h: 6 },
};

export const UNDERLYINGS: readonly string[] = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"];
export const DEFAULT_UNDERLYING = "NIFTY";

const p = (id: PanelId, x: number, y: number, w: number, h: number, open = true, symbol: string | null = null): PanelPlace =>
  ({ id, x, y, w, h, open, symbol });

export const PRESETS: Record<PresetId, { name: string; description: string; panels: PanelPlace[] }> = {
  option_seller: {
    name: "Option seller",
    description: "The chart, the option chain and the order panel side by side; your positions, basket and orders below.",
    // T1-3: the chain gets half of the twelve columns, so its OI / change in OI / IV / delta / LTP per side fit on one line.
    panels: [p("chart-1", 0, 0, 3, 11), p("chain", 3, 0, 6, 11), p("order", 9, 0, 3, 11),
      p("positions", 0, 11, 5, 7), p("basket", 5, 11, 3, 7), p("orders", 8, 11, 4, 7),
      p("bots", 0, 18, 6, 6, false), p("payoff", 6, 18, 6, 6, false)],
  },
  quick_trade: {
    name: "Quick trade",
    description: "A large chart with the order panel beside it; your positions and orders below.",
    panels: [p("chart-1", 0, 0, 8, 12), p("order", 8, 0, 4, 12), p("positions", 0, 12, 8, 7), p("orders", 8, 12, 4, 7),
      p("chain", 0, 19, 3, 11, false), p("basket", 3, 19, 3, 7, false)],
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
export type PhoneTab = "chart" | "chain" | "order" | "positions" | "orders" | "bots";
export const PHONE_TABS: readonly { key: PhoneTab; label: string }[] = [
  { key: "chart", label: "Chart" }, { key: "chain", label: "Chain" }, { key: "order", label: "Order" },
  { key: "positions", label: "Positions" }, { key: "orders", label: "Orders" },
];
