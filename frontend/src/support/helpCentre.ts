import { GLOSSARY } from "../ui/glossary";
import type { TourId } from "../tours/pages";
import type { PageTour, WelcomeCard } from "../tours/types";

/**
 * C12: the Help centre's articles and its search. Everything comes from content that already exists, so a text is
 * fixed in one place:
 * - "How to": the help pages (`backend/app/support/content/help.md`, the same text the help bot answers from), read
 *   from `GET /api/support/help`;
 * - "Words": the glossary behind every "?" on the v2 pages (`src/ui/glossary.ts`);
 * - "Page tips": the page tours and the welcome cards (`src/tours/content/`).
 * Education only: how to use the app and what a word means - never a security name, a price or what to trade (a test
 * checks the built list).
 */

export type HelpKind = "how-to" | "word" | "page-tip";

export interface HelpArticle {
  id: string;
  kind: HelpKind;
  title: string;
  /** Plain text; a page tip has one line per step. */
  text: string;
  /** The app page it is about, when there is one. */
  page?: string;
  /** Other words people use for it (searched, never shown). */
  keywords: string;
}

export const KIND_LABEL: Record<HelpKind, string> = { "how-to": "How to", word: "Words", "page-tip": "Page tips" };

/** A help page from the server (`GET /api/support/help`). */
export interface ServerHelpArticle { id: string; title: string; text: string; page: string; keywords: string }

/** Where each page tour belongs, as the user knows the page. */
export const TOUR_PAGES: Record<TourId, { name: string; path: string }> = {
  dashboard: { name: "Today", path: "/" },
  broker: { name: "Broker", path: "/broker" },
  charts: { name: "Charts", path: "/charts" },
  "option-chain": { name: "Option chain", path: "/option-chain" },
  signals: { name: "Signals", path: "/signals" },
  positions: { name: "My trades", path: "/positions" },
  portfolio: { name: "Money", path: "/positions/money" },
  risk: { name: "Risk limits", path: "/risk-management" },
  settings: { name: "Settings", path: "/settings" },
  copilot: { name: "AI Copilot", path: "/copilot" },
};

const safePage = (page: string | undefined): string | undefined =>
  page && page.startsWith("/") && !page.startsWith("//") ? page : undefined;

export function howToArticles(list: readonly ServerHelpArticle[]): HelpArticle[] {
  return list.map((a) => ({ id: a.id, kind: "how-to" as const, title: a.title, text: a.text, page: safePage(a.page), keywords: a.keywords ?? "" }));
}

export function glossaryArticles(): HelpArticle[] {
  return Object.entries(GLOSSARY).map(([key, entry]) => ({ id: `word:${key}`, kind: "word" as const, title: entry.term, text: entry.definition, keywords: key.replace(/-/g, " ") }));
}

export function tourArticles(tours: Readonly<Record<TourId, PageTour>>, welcome: readonly WelcomeCard[]): HelpArticle[] {
  const out: HelpArticle[] = [];
  for (const [id, tour] of Object.entries(tours) as [TourId, PageTour][]) {
    const where = TOUR_PAGES[id];
    const seen = new Set<string>();
    const lines: string[] = [];
    for (const step of tour.steps) {
      if (seen.has(step.title)) continue;           // the phone and desktop versions of one step
      seen.add(step.title);
      lines.push(`${step.title}: ${step.body}`);
    }
    out.push({ id: `tips:${id}`, kind: "page-tip", title: `${where.name}: what is on this page`, text: lines.join("\n"), page: where.path,
               keywords: `${id.replace(/-/g, " ")} tour tips` });
  }
  out.push({ id: "tips:welcome", kind: "page-tip", title: "Getting started", text: welcome.map((c) => `${c.title}: ${c.body}`).join("\n"),
             keywords: "welcome start first steps begin new" });
  return out;
}

/** Lower case words; a trailing plural "s" dropped so "lots" finds "lot". */
export function words(text: string): string[] {
  return (text.toLowerCase().match(/[a-z0-9&:]+/g) ?? []).map((w) => (w.length > 3 && w.endsWith("s") && !w.endsWith("ss") ? w.slice(0, -1) : w));
}

const ORDER: Record<HelpKind, number> = { "how-to": 0, "page-tip": 1, word: 2 };

/**
 * The articles that match every word of the query (a word matches the start of a word in the title, the keywords or
 * the text), best first: a title hit counts 3, a keyword 2, the text 1. An empty query lists everything, how-to first.
 */
export function searchHelp(articles: readonly HelpArticle[], query: string): HelpArticle[] {
  const wanted = words(query).filter((w) => w.length >= 2 || /\d/.test(w));
  if (wanted.length === 0) {
    return [...articles].sort((a, b) => ORDER[a.kind] - ORDER[b.kind] || a.title.localeCompare(b.title));
  }
  const scored: { a: HelpArticle; score: number }[] = [];
  for (const a of articles) {
    const parts: [number, string[]][] = [[3, words(a.title)], [2, words(a.keywords)], [1, words(a.text)]];
    let score = 0;
    let all = true;
    for (const w of wanted) {
      let best = 0;
      for (const [weight, list] of parts) if (weight > best && list.some((x) => x.startsWith(w))) best = weight;
      if (!best) { all = false; break; }
      score += best;
    }
    if (all) scored.push({ a, score });
  }
  return scored.sort((x, y) => y.score - x.score || ORDER[x.a.kind] - ORDER[y.a.kind] || x.a.title.localeCompare(y.a.title)).map((s) => s.a);
}
