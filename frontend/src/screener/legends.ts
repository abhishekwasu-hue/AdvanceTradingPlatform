/**
 * U7 LG1: the Legends library as the server sends it (GET /api/screener/legends) - per card its four lines with the
 * public sources each one cites, the source line, the disclaimer naming the person, what the screen approximates and
 * the screen itself (ScreenQL source, base timeframe, editable parameters with documented ranges). Nothing here is
 * hardcoded: every word comes from the server's reviewed data files. Pure functions only.
 *
 * Educational only: a card describes a published method; it never ranks, scores or recommends anything.
 */

import { keepScreens, type Taxonomy } from "./taxonomy";

/** The template source the Legends screens come from (backend `app/screener/legends.py`). */
export const LEGENDS_SOURCE = "legends";

export type LegendLineKey = "who" | "style" | "looks_for" | "timeframe";
export const LINE_KEYS: LegendLineKey[] = ["who", "style", "looks_for", "timeframe"];
/** How each line is introduced on a card (the visible term of the description list). */
export const LINE_LABELS: Record<LegendLineKey, string> = { who: "Who", style: "Style", looks_for: "This screen looks for", timeframe: "Timeframe" };

export interface LegendLine { text: string; cite: string[] }
export interface LegendSource {
  id: string; kind: string; title: string; authors: string[]; year: number; publisher: string; covers: string; citation: string;
  /** LG1-2: the bibliographic check against public catalogue records - "verified" | "unverified", why, and the record URLs. */
  review: string; review_note: string; evidence: string[];
}
/** LG1-2: "verified" when every source the card cites is verified; otherwise "unverified" and the note says why. */
export interface LegendReview { status: "verified" | "unverified"; note: string }
export interface LegendParam { name: string; default: number; min: number; max: number; doc: string }
export interface LegendCardData {
  id: string;
  legend: string;
  name: string;
  lines: Record<LegendLineKey, LegendLine>;
  source_line: string;
  sources: LegendSource[];
  approximation: string;
  disclaimer: string;
  review: LegendReview;
  screen: { id: string; source: string; base_tf: string; params: LegendParam[] };
  filing: { method: string; setup: string; tags: Record<string, unknown> } | null;
}
export interface LegendsLibrary { version: string; header: string; note: string; cards: LegendCardData[] }

/** A usable library: a header and a list of cards, each with its lines, disclaimer and screen (anything else - an
 * older server, a proxy page - shows no Legends detail rather than a broken one). */
export function isLegends(x: unknown): x is LegendsLibrary {
  const l = x as Partial<LegendsLibrary> | null;
  return !!l && typeof l.header === "string" && Array.isArray(l.cards)
    && l.cards.every((c) => !!c && typeof c.disclaimer === "string" && !!c.lines && !!c.screen && Array.isArray(c.sources));
}

export function cardFor(lib: LegendsLibrary | null | undefined, screenId: string): LegendCardData | null {
  return lib?.cards.find((c) => c.id === screenId) ?? null;
}

/** The card's five lines in order: who, style, what the screen looks for, timeframe, and the source line. */
export function cardLines(card: LegendCardData): { key: LegendLineKey | "source"; label: string; text: string }[] {
  return [...LINE_KEYS.map((k) => ({ key: k, label: LINE_LABELS[k], text: card.lines[k].text })), { key: "source" as const, label: "Source", text: card.source_line.replace(/^Source:\s*/, "") }];
}

/** "0.5 to 1" - a parameter's documented range, in plain numbers. */
export function rangeText(p: LegendParam): string {
  return `${p.min} to ${p.max}`;
}

/** The ScreenQL text with every `$parameter` shown at its default, for reading (the stored screen keeps the names). */
export function filledSource(card: LegendCardData): string {
  const byName = new Map(card.screen.params.map((p) => [p.name, p.default]));
  return card.screen.source.replace(/\$([A-Za-z_][A-Za-z0-9_]*)/g, (whole, name: string) => (byName.has(name) ? String(byName.get(name)) : whole));
}

/** L1 (review): a Legends screen is listed only with its card, so it always carries its disclaimer. While the library
 * is missing (its call failed, an older server) the Legends screens leave the gallery and its counts. */
export function withLegendCards(tax: Taxonomy, lib: LegendsLibrary | null | undefined): Taxonomy {
  return keepScreens(tax, (s) => s.source !== LEGENDS_SOURCE || cardFor(lib, s.id) !== null);
}
