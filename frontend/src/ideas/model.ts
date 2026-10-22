/** Idea library slice 1: the page's pure helpers - the group filter, the beginner suggestions, the scan hand-off. */
import type { BeginnerQuestion, Idea, IdeaScan, IdeaSetting } from "./api";

export const IDEAS_ADDRESS = "/ideas";
export const IDEA_FLAG = "idea_library";
export const ALL_GROUPS = "all";
/** At most this many ideas are suggested by "Not sure where to start?" (the order: two or three). */
export const MAX_SUGGESTIONS = 3;

/** The ideas of one group (or all of them), in the library's order. */
export function ideasIn(ideas: readonly Idea[], group: string): Idea[] {
  return group === ALL_GROUPS ? [...ideas] : ideas.filter((i) => i.group === group);
}

/**
 * "Not sure where to start?": each answer names ideas, the first being its lead. The suggestion is each answer's lead
 * idea (in question order), then the ideas most answers name (ties keep the library's order, the owner's build order),
 * two or three in all. So an option seller always sees the options pack. Nothing until every question is answered.
 * A reading list - never advice.
 */
export function suggestIdeas(questions: readonly BeginnerQuestion[], answers: Record<string, string>, order: readonly string[]): string[] {
  if (!questions.length || questions.some((q) => !answers[q.id])) return [];
  const picked = questions.map((q) => q.options.find((o) => o.id === answers[q.id])?.ideas ?? []);
  const out: string[] = [];
  for (const ideas of picked) if (ideas[0] && !out.includes(ideas[0])) out.push(ideas[0]);
  const score = new Map<string, number>();
  for (const ideas of picked) for (const id of ideas) score.set(id, (score.get(id) ?? 0) + 1);
  const rest = [...score.entries()].filter(([id]) => !out.includes(id))
    .sort((a, b) => b[1] - a[1] || order.indexOf(a[0]) - order.indexOf(b[0])).map(([id]) => id);
  return [...out, ...rest].slice(0, MAX_SUGGESTIONS);
}

/** Where "Try as a scan" goes: the Screener, which reads the idea and opens the scan in its builder. */
export function screenerPathFor(ideaId: string, scanId: string): string {
  return `/screener?${new URLSearchParams({ idea: ideaId, scan: scanId }).toString()}`;
}

/** The idea scan an address asks the Screener to open (`?idea=T1&scan=up`), or null. */
export function ideaScanFromSearch(search: string): { idea: string; scan: string } | null {
  const q = new URLSearchParams(search);
  const idea = q.get("idea") ?? "";
  const scan = q.get("scan") ?? "";
  return /^T[1-8]$/.test(idea) && /^[a-z][a-z0-9_]{0,31}$/.test(scan) ? { idea, scan } : null;
}

/** The scan's name in the Screener: "Breakout and retest - Retest from above". */
export function scanName(idea: Pick<Idea, "name">, scan: Pick<IdeaScan, "label">): string {
  return `${idea.name} - ${scan.label}`;
}

/** A setting's starting value in words: the choice's label, or the number. */
export function defaultText(s: IdeaSetting): string {
  if (s.type === "choice") return s.choices.find((c) => c.value === s.default)?.label ?? String(s.default);
  return String(s.default);
}

/** A number setting's range in words ("0.1 to 5"); empty for a choice. */
export function rangeText(s: IdeaSetting): string {
  return s.type === "number" && s.min !== null && s.max !== null ? `${s.min} to ${s.max}` : "";
}
