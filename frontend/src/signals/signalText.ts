import { hasPrice, isClean } from "./language";

/**
 * UX3 fix 10 (UX audit §1 problem 8, CLAUDE.md "Signal language"): the one formatter for what a signal SAYS on the
 * Signals page and the Dashboard's Active signals. Signal text is structure words only: the setup in words, never a
 * single price, never a buy / sell / target / stop-loss word. The user's own levels (the numbers his approval needs) are
 * not signal text: they sit one tap away in the detail's "Your levels" fold, with neutral labels (LEVEL_LABELS).
 *
 * The call-word and price checks live in the one signal-language module (./language.ts: `isClean`, `hasPrice`), the
 * same list Analyze a chart uses, so the surfaces never disagree; a price is any figure a trader would read as one.
 */

/** Shown when the setup's own name reads as a call ("Buy the dip") or names a price. */
export const SETUP_FALLBACK = "A setup from your strategy";

export { hasPrice };

/** True when the text may be shown as signal text: no call word and no price. */
export function isStructureText(text: string): boolean {
  const t = text.trim();
  return t !== "" && isClean(t) && !hasPrice(t);
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
