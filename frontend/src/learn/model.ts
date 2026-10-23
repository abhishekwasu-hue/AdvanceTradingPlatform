/** Readiness C13: the Learn page's pure helpers - its address, the glossary shown on it and the safe video link. */
import { GLOSSARY, type GlossaryEntry } from "../ui/glossary";
import { callWords } from "../signals/language";
import type { Lesson } from "./api";

export const LEARN_ADDRESS = "/learn";
export const LEARN_FLAG = "learn";
export const ALL = "all";

/** Index and security names an education surface never shows (the backend content guard's list, the common ones). */
const SECURITY_NAMES = /\b(?:nifty|banknifty|finnifty|midcpnifty|sensex|bankex|reliance|tcs|infosys|hdfc|icici|sbi)\b/i;

/**
 * The glossary as the Learn page shows it, A to Z. An education surface carries no security names and no trade-call
 * words (CLAUDE.md signal language), so an entry whose term or definition holds one stays in the "?" hints of the pages
 * that use it and is left out here.
 */
export function learnGlossary(query = ""): (GlossaryEntry & { key: string })[] {
  const q = query.trim().toLowerCase();
  return Object.entries(GLOSSARY)
    .map(([key, entry]) => ({ key, ...entry }))
    .filter((e) => callWords(`${e.term} ${e.definition}`).length === 0 && !SECURITY_NAMES.test(`${e.term} ${e.definition}`))
    .filter((e) => !q || e.term.toLowerCase().includes(q) || e.definition.toLowerCase().includes(q))
    .sort((a, b) => a.term.localeCompare(b.term));
}

/** The lessons of one group (or all), in the library's order. */
export function lessonsIn(lessons: readonly Lesson[], group: string): Lesson[] {
  return group === ALL ? [...lessons] : lessons.filter((l) => l.group === group);
}

/** The lessons whose title, summary or points mention the words typed. */
export function searchLessons(lessons: readonly Lesson[], query: string): Lesson[] {
  const q = query.trim().toLowerCase();
  if (!q) return [...lessons];
  return lessons.filter((l) => [l.title, l.summary, ...l.points].some((t) => t.toLowerCase().includes(q)));
}

/** A video link is shown only when it is a plain https address (the server checks it too). */
export function safeVideoUrl(url: string | null | undefined): string | null {
  if (!url) return null;
  try {
    const u = new URL(url);
    return u.protocol === "https:" && !u.username && !u.password ? u.toString() : null;
  } catch {
    return null;
  }
}

/** An address inside the app ("/charts"), never another site. */
export function isAppPath(path: string): boolean {
  return /^\/[a-z0-9][a-z0-9/-]*$/.test(path) && !path.includes("//");
}
