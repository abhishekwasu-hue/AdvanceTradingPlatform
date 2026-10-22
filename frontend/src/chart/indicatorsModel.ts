/**
 * UI2-4b2 (UI v2 proposal §6, Charts page plan §3): what the Indicators menu lists and what a choice changes, free of
 * React. Three groups:
 * - "On this chart": the volume and RSI panes (on by default, CHP-13); turning one off removes its pane;
 * - "Popular": price lines drawn over the candles, each with a glossary "?";
 * - "Layers": extra information from other features, each listed only while its flag is on (§9 rule 9).
 * The search matches the name and a few plain words ("average", "momentum").
 */
import { DEFAULT_SETTINGS } from "../utils/indicators";
import type { GlossaryKey } from "../ui/glossary";
import { OVERLAY_LABELS_V2 as L, type OverlayId, type PaneChoice } from "./chartCore";

export type MenuGroup = "chart" | "popular" | "layers";
export type PaneId = keyof PaneChoice;

export interface MenuItem {
  id: PaneId | OverlayId | string;
  group: MenuGroup;
  label: string;
  /** The glossary "?" next to the name. */
  term?: GlossaryKey;
  /** One plain line under the name (layers). */
  note?: string;
  /** A layer's feature flag. */
  flag?: string;
  keywords: string;
}

export const GROUP_TITLES: Record<MenuGroup, string> = { chart: "On this chart", popular: "Popular", layers: "Layers" };

export const MENU_ITEMS: readonly MenuItem[] = [
  { id: "volume", group: "chart", label: "Volume", term: "volume", keywords: "volume bars traded" },
  { id: "rsi", group: "chart", label: `RSI ${DEFAULT_SETTINGS.rsiPeriod}`, term: "rsi", keywords: "rsi relative strength momentum oscillator" },
  { id: "ema_fast", group: "popular", label: L.ema_fast, term: "ema", keywords: "ema exponential moving average fast" },
  { id: "ema_slow", group: "popular", label: L.ema_slow, term: "ema", keywords: "ema exponential moving average slow" },
  { id: "sma", group: "popular", label: L.sma, term: "sma", keywords: "sma simple moving average" },
  { id: "vwap", group: "popular", label: L.vwap, term: "vwap", keywords: "vwap volume weighted average price" },
  { id: "trades", group: "layers", label: "My trades", note: "Your paper trades, where they were entered and closed.", flag: "chart_trades_layer",
    keywords: "my trades paper entries exits orders" },
];

/** What the menu reads and changes. */
export interface IndicatorState { panes: PaneChoice; overlays: readonly OverlayId[]; layers: readonly string[] }

const isPane = (id: string): id is PaneId => id === "volume" || id === "rsi";

/** The groups to show for a search, in order, each with its items; an empty group is left out. */
export function menuGroups(query: string, on: (flag: string) => boolean): { group: MenuGroup; title: string; items: MenuItem[] }[] {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const match = (i: MenuItem) => words.every((w) => `${i.label} ${i.keywords}`.toLowerCase().includes(w));
  return (Object.keys(GROUP_TITLES) as MenuGroup[])
    .map((group) => ({ group, title: GROUP_TITLES[group], items: MENU_ITEMS.filter((i) => i.group === group && (!i.flag || on(i.flag)) && match(i)) }))
    .filter((g) => g.items.length > 0);
}

export function isOn(id: string, s: IndicatorState): boolean {
  if (isPane(id)) return s.panes[id];
  return (s.overlays as readonly string[]).includes(id) || s.layers.includes(id);
}

/** What turning `id` on or off changes: a pane goes to the server prefs, a price line or a layer to the view. */
export function toggle(id: string, s: IndicatorState):
  { panes: Partial<PaneChoice> } | { overlays: OverlayId[] } | { layers: string[] } {
  const next = !isOn(id, s);
  if (isPane(id)) return { panes: { [id]: next } };
  const item = MENU_ITEMS.find((i) => i.id === id);
  if (item?.group === "popular") {
    const set = new Set<string>(s.overlays);
    if (next) set.add(id); else set.delete(id);
    return { overlays: MENU_ITEMS.filter((i) => i.group === "popular" && set.has(i.id)).map((i) => i.id as OverlayId) };
  }
  return { layers: next ? [...s.layers, id] : s.layers.filter((l) => l !== id) };
}

/** How many are on, for the button ("Indicators · 3"). Layers whose flag is off do not count. */
export function onCount(s: IndicatorState, on: (flag: string) => boolean): number {
  return MENU_ITEMS.filter((i) => (!i.flag || on(i.flag)) && isOn(i.id, s)).length;
}
