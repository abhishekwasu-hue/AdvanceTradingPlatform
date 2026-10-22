import { hasCallWords } from "../chartAnalyze/signalLanguage";

/**
 * UX3 fix 10 (UX audit §1 problem 8, CLAUDE.md "Signal language"): the one formatter for what a signal SAYS on the
 * Signals page and the Dashboard's Active signals. Signal text is structure words only: the setup in words, never a
 * single price, never a buy / sell / target / stop-loss word. The user's own levels (the numbers his approval needs) are
 * not signal text: they sit one tap away in the detail's "Your levels" fold, with neutral labels (LEVEL_LABELS).
 *
 * The same call-word list as Analyze a chart (src/chartAnalyze/signalLanguage.ts, backend
 * app/chart_analyze/signal_language.py), so the two never disagree; a price is any figure a trader would read as one.
 */

/** Shown when the setup's own name reads as a call ("Buy the dip") or names a price. */
export const SETUP_FALLBACK = "A setup from your strategy";

/** A price-like figure: "24,790", "24790", "1,35,000.50", "99.5". Small whole numbers ("EMA 20", "15m") are not prices. */
const PRICE = /(?:\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\b\d{3,}(?:\.\d+)?\b|\b\d+\.\d+\b)/;

/** True when the text carries a single price. */
export function hasPrice(text: string): boolean {
  return PRICE.test(text);
}

/** True when the text may be shown as signal text: no call word and no price. */
export function isStructureText(text: string): boolean {
  const t = text.trim();
  return t !== "" && !hasCallWords(t) && !hasPrice(t);
}

/** The setup as shown: its own words when they are structure words, else the plain fallback. */
export function setupWords(setup: string | null | undefined): string {
  const t = (setup ?? "").trim();
  return isStructureText(t) ? t : SETUP_FALLBACK;
}

/** "Why this signal": only the reasons in structure words; a reason that names a price or reads as a call is left out
 * (the chart shows the levels). Duplicates are dropped. */
export function reasonWords(reasons: readonly string[]): string[] {
  const out: string[] = [];
  for (const r of reasons) {
    const t = r.trim();
    if (isStructureText(t) && !out.includes(t)) out.push(t);
  }
  return out;
}

/** The neutral names of the user's own levels (his approval's numbers, never signal text). */
export const LEVEL_LABELS = { entry: "Entry", stop: "Exit if wrong", target: "Exit at profit" } as const;
