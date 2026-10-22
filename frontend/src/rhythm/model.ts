import { hasCallWords } from "../chartAnalyze/signalLanguage";
import type { RhythmBrief, RhythmKind, RhythmPrefs, RhythmSection } from "./types";

/**
 * MO4: the daily rhythm card's pure parts. The server builds each digest in structure words (no buy / sell / target /
 * stop wording, no price in a setup line); the card checks every line again on render with the same word list as
 * Analyze a chart, and leaves out a line that fails rather than showing it.
 */

/** The card's four tabs: short, so they fit side by side on a 360 px phone (the card's title says the full name). */
export const TAB_LABELS: Record<RhythmKind, string> = {
  pre_market: "Morning",
  mid_session: "Pulse",
  closing: "Wrap",
  weekly: "Week",
};

export const KIND_ORDER: readonly RhythmKind[] = ["pre_market", "mid_session", "closing", "weekly"];

/** How often the card reads the digest again (the server caches it for a minute). */
export const RHYTHM_REFRESH_MS = 5 * 60_000;

/** A section with only the lines that pass the signal-language check. */
export function cleanSection(section: RhythmSection): RhythmSection {
  return { ...section, lines: section.lines.filter((l) => !hasCallWords(l)) };
}

export function cleanBrief(brief: RhythmBrief): RhythmBrief {
  return { ...brief, sections: brief.sections.map(cleanSection) };
}

export const TELEGRAM_CHOICES: readonly { key: Exclude<keyof RhythmPrefs, "telegram">; label: string; line: string }[] = [
  { key: "pre_market", label: "Before the open", line: "Trading days, before 09:15" },
  { key: "mid_session", label: "Mid-session pulse", line: "Trading days, around 12:30 (optional)" },
  { key: "closing", label: "Closing wrap", line: "Trading days, after the close" },
  { key: "weekly", label: "Weekly review", line: "Saturday morning" },
];

export const DEFAULT_PREFS: RhythmPrefs = { telegram: false, pre_market: true, mid_session: false, closing: true, weekly: true };
