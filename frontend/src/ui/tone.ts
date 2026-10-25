import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

/**
 * UI-H colour accents CA4 (owner order): the tone of a summary card - a soft tinted background by meaning. Colour is
 * never the only signal: a tone ALWAYS comes with a sign (+ / −) in the value, an icon, or a word. The types make a
 * tone without a cue impossible to write, and `checkTone` refuses one at runtime (a value with no sign, an empty word).
 */
export type SummaryTone = "profit" | "loss" | "risk" | "info";

/** What carries the meaning besides the colour. `sign`: the value itself starts with + / − (a string or a number). */
export type ToneCue = { sign: true } | { icon: LucideIcon } | { word: string };

/** Either no tone at all, or a tone with its cue - never a tone alone. */
export type Toned = { tone?: undefined; cue?: undefined } | { tone: SummaryTone; cue: ToneCue };

// Written out in full so Tailwind sees every class.
const SURFACE: Record<SummaryTone, string> = {
  profit: "bg-tone-profit",
  loss: "bg-tone-loss",
  risk: "bg-tone-risk",
  info: "bg-tone-info",
};

export function toneSurfaceClass(tone: SummaryTone): string {
  return SURFACE[tone];
}

const SIGNED = /^\s*[+\-−]/;

/** Why a tone may not show: no cue, a `sign` cue on a value without a sign, or an empty `word` cue (null: it may). */
export function toneProblem(tone: SummaryTone | undefined, cue: ToneCue | undefined, value?: ReactNode): string | null {
  if (tone === undefined) return null;
  if (!cue) return `A "${tone}" tone needs a sign, an icon or a word as well - colour alone is never enough.`;
  if ("sign" in cue) {
    const text = typeof value === "number" ? (value > 0 ? `+${value}` : String(value)) : value;
    if (typeof text !== "string" || !SIGNED.test(text)) return `A "${tone}" tone with a sign cue needs a value that starts with + or −.`;
  }
  if ("word" in cue && !cue.word.trim()) return `A "${tone}" tone's word cue is empty.`;
  return null;
}

/**
 * True when the tone may show. A tone without its cue throws in development and in the tests (so it cannot ship);
 * in a production build the card simply drops the tint (the page keeps working) and the problem is logged.
 */
export function checkTone(tone: SummaryTone | undefined, cue: ToneCue | undefined, value?: ReactNode): boolean {
  const problem = toneProblem(tone, cue, value);
  if (problem === null) return tone !== undefined;
  if (import.meta.env.PROD) {
    console.error(problem);
    return false;
  }
  throw new Error(problem);
}
