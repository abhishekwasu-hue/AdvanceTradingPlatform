import type { Page } from "../components/Sidebar";

/**
 * UX1 (handover 8b, audit section 7): the pages that exist only while an operator feature flag is on. Both shells read
 * this one map: a page whose flag is off is left out of the navigation (sidebar, phone bar, More sheet, command bar),
 * and its address shows "Coming soon" - never the flag name to a trader (an administrator sees it in a small note).
 * A tiny module on purpose: today's shell (App.tsx) reads it too, so it must not pull the v2 navigation in.
 */
export const PAGE_FLAGS: Readonly<Partial<Record<Page | "ideas" | "markets" | "learn", string>>> = {
  copilot: "ai_copilot",
  journal: "discipline_engine",
  // MO4 slice 2: the Sector & index view (v2 only, /markets).
  markets: "markets_view",
  marketplace: "marketplace",
  "options-builder": "options_builder",
  // Audit P06: Find setups is empty while the screener is off - it leaves the navigation until `screener_v2` is on.
  screener: "screener_v2",
  // Idea library slice 1: the v2-only Ideas page (/ideas) while `idea_library` is off.
  ideas: "idea_library",
  // Readiness C13: the v2-only Learn page (/learn) while `learn` is off.
  learn: "learn",
};

/** What the shell knows about the flags (useFeatures): `answered` is a real answer from the server. */
export interface FlagsKnown {
  answered: boolean;
  on: (flag: string) => boolean;
}

/**
 * The flag that keeps `page` closed right now, or null when the page may show. Only a real answer closes a page: while
 * the flags are not known, or the read failed (today's shell then works as before), nothing is closed here - the v2
 * shell renders only after a real answer anyway (it needs `ui_v2`), and every endpoint behind a flag refuses on the
 * server whatever the client shows.
 */
export function closedFlag(page: Page | string | null, flags: FlagsKnown): string | null {
  if (!page) return null;
  const flag = PAGE_FLAGS[page as keyof typeof PAGE_FLAGS];
  if (!flag || !flags.answered) return null;
  return flags.on(flag) ? null : flag;
}
