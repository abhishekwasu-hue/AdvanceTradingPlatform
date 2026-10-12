/**
 * U9 TX1: the screen library's taxonomy as the server sends it (GET /api/screener/taxonomy) - two axes (method x setup
 * type), the tag vocabulary, the pre-built screens filed on both and the matrix of counts. Nothing here is hardcoded:
 * every label, definition and count comes from the server's reviewed data file. Pure functions only, so the gallery's
 * filtering is tested without a browser.
 *
 * Informational only: the library files screens, it never ranks or recommends one.
 */

export interface TaxAxis { id: string; code: string; label: string; definition: string; user_built: boolean }
export interface TagValue { id: string; label: string }
export type TagFamily = "timeframe_family" | "direction" | "data_tier" | "difficulty" | "universe_default";

export interface TaxScreen {
  id: string;
  source: string;
  name: string;
  definition: string;
  method: string;
  setup: string;
  tags: { timeframe_family: string[]; direction: string; data_tier: string[]; difficulty: string; universe_default: string };
  evidence_ready: boolean;
  fitness_available: boolean;
}

export interface Taxonomy {
  version: string;
  methods: TaxAxis[];
  setups: TaxAxis[];
  tags: Record<TagFamily, TagValue[]>;
  screens: TaxScreen[];
  matrix: Record<string, Record<string, number>>;   // method -> setup -> screens filed there (0 = an empty cell)
  total: number;
  note: string;
}

/** The response has both axes, a matrix and a screen list (anything else - an older server, a proxy page - shows no
 * gallery rather than a broken one). */
export function isTaxonomy(x: unknown): x is Taxonomy {
  const t = x as Partial<Taxonomy> | null;
  return !!t && Array.isArray(t.methods) && Array.isArray(t.setups) && Array.isArray(t.screens) && !!t.matrix && typeof t.matrix === "object" && !!t.tags;
}

export interface GalleryFilter { method: string | null; setup: string | null; query: string }
export const NO_FILTER: GalleryFilter = { method: null, setup: null, query: "" };

/** Screens in one cell, one row (setup null), one column (method null) or the whole library (both null). */
export function cellCount(tax: Taxonomy, method: string | null, setup: string | null): number {
  let n = 0;
  for (const m of tax.methods) {
    if (method !== null && m.id !== method) continue;
    const row = tax.matrix[m.id] ?? {};
    for (const s of tax.setups) {
      if (setup !== null && s.id !== setup) continue;
      n += row[s.id] ?? 0;
    }
  }
  return n;
}

function matchesQuery(s: TaxScreen, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return q.split(/\s+/).every((word) => s.name.toLowerCase().includes(word) || s.definition.toLowerCase().includes(word));
}

/** The cards to show: the picked method (rail) and setup (chips), then the search over names and definitions. Order
 * is the data file's (a filing order, not a ranking). */
export function filterScreens(tax: Taxonomy, f: GalleryFilter): TaxScreen[] {
  return tax.screens.filter((s) => (f.method === null || s.method === f.method) && (f.setup === null || s.setup === f.setup) && matchesQuery(s, f.query));
}

export function axisLabel(axes: TaxAxis[], id: string | null, all: string): string {
  if (id === null) return all;
  return axes.find((a) => a.id === id)?.label ?? id;
}

export function tagLabel(tax: Taxonomy, family: TagFamily, id: string): string {
  return tax.tags[family]?.find((t) => t.id === id)?.label ?? id;
}

/** Where the trader is looking, in words: "Indicator → Momentum / relative strength". */
export function placeText(tax: Taxonomy, f: GalleryFilter): string {
  return `${axisLabel(tax.methods, f.method, "All methods")} → ${axisLabel(tax.setups, f.setup, "All setups")}`;
}

/** An honest empty state: says which cell is empty, and why a user-built folder has nothing in it. */
export function emptyText(tax: Taxonomy, f: GalleryFilter): string {
  const method = tax.methods.find((m) => m.id === f.method);
  if (f.query.trim() && cellCount(tax, f.method, f.setup) > 0) return `No screen in ${placeText(tax, f)} matches "${f.query.trim()}".`;
  if (method?.user_built) return `${method.label} holds the screens you build and save; the gallery does not list them yet.`;
  if (f.method === null && f.setup === null) return "No screens are filed yet.";
  return `No screens are filed under ${placeText(tax, f)} yet.`;
}

/** The count a rail entry shows: its screens within the picked setup chip. */
export function railCount(tax: Taxonomy, method: string | null, f: GalleryFilter): number {
  return cellCount(tax, method, f.setup);
}

/** The count a setup chip shows: its screens within the picked method. */
export function chipCount(tax: Taxonomy, setup: string | null, f: GalleryFilter): number {
  return cellCount(tax, f.method, setup);
}

/** The taxonomy with only the screens `keep` accepts, its matrix and total recomputed (every cell still present). */
export function keepScreens(tax: Taxonomy, keep: (s: TaxScreen) => boolean): Taxonomy {
  const screens = tax.screens.filter(keep);
  if (screens.length === tax.screens.length) return tax;
  const removed = tax.screens.filter((s) => !keep(s));
  const matrix = Object.fromEntries(Object.entries(tax.matrix).map(([m, row]) => [m, { ...row }]));
  for (const s of removed) if (matrix[s.method]?.[s.setup] !== undefined) matrix[s.method][s.setup] = Math.max(0, matrix[s.method][s.setup] - 1);
  return { ...tax, screens, matrix, total: Math.max(0, tax.total - removed.length) };
}
