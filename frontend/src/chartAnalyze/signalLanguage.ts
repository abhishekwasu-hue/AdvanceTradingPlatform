import type { AnalysisResult, StrikesAnswer } from "./types";
import { CALL_WORDS, SL_WORD } from "../signals/language";

/**
 * Analyze a chart: the signal-language check on everything the result screen shows (CLAUDE.md §3). Result text uses
 * structure words only - no buy / sell, no target, no stop / stop-loss. The server withholds such a text already
 * (backend app/chart_analyze/signal_language.py, the same list); this runs again on render, so a result stored before
 * that check, or any future field, is guarded too. "buyer" / "seller" (the area sides) are structure words: whole words
 * only are matched.
 */

// Handover 11l: the list lives in the one shared signal-language module (src/signals/language.ts); the same answers.
const WORDS = CALL_WORDS;
const SL = SL_WORD;

export const WITHHELD = "(This text was withheld: it read as advice rather than a description.)";

export function hasCallWords(text: string): boolean {
  return WORDS.test(text) || SL.test(text);
}

/** The text itself, or the withheld line when it carries a call word. */
export function guardText(text: string): string {
  return text && hasCallWords(text) ? WITHHELD : text;
}

/** Every string in a value, guarded (deep); `keep` names keys left as they are (the server's fixed disclaimer). */
export function guardDeep<T>(value: T, keep: ReadonlySet<string> = new Set()): T {
  if (typeof value === "string") return guardText(value) as T;
  if (Array.isArray(value)) return value.map((v) => guardDeep(v, keep)) as T;
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(value)) out[k] = keep.has(k) ? v : guardDeep(v, keep);
    return out as T;
  }
  return value;
}

/** The disclaimer is the one fixed text that names "buy or sell" - to say the result is NOT that. It is left as sent. */
const KEEP = new Set(["disclaimer"]);

export function guardResult(result: AnalysisResult): AnalysisResult {
  return guardDeep(result, KEEP);
}

export function guardStrikes(answer: StrikesAnswer): StrikesAnswer {
  return guardDeep(answer);
}
