import type { Page } from "../components/Sidebar";

/**
 * UX1 (handover 8b, audit section 7): the pages that exist only while an operator feature flag is on. Both shells read
 * this one map: a page whose flag is off is left out of the navigation (sidebar, phone bar, More sheet, command bar),
 * and its address shows "Coming soon" - never the flag name to a trader (an administrator sees it in a small note).
 * A tiny module on purpose: today's shell (App.tsx) reads it too, so it must not pull the v2 navigation in.
 */
export const PAGE_FLAGS: Readonly<Partial<Record<Page | "ideas" | "markets" | "learn" | "terminal", string>>> = {
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
  // User-friendly pass item 7: Analyze a chart (the server also answers it as off while its engine is not installed).
  "chart-analyze": "chart_analyze",
  // Readiness C13: the v2-only Learn page (/learn) while `learn` is off.
  learn: "learn",
  // Trading Terminal T1: the v2-only Terminal (/terminal) while `terminal` is off (ON by default since the owner's ✅ 25 Oct).
  terminal: "terminal",
};

/**
 * A1 (UI-H bug lane): pages that show their own closed state at their address, because the flag alone cannot say why.
 * Analyze a chart is answered as off while its engine is not installed: the page then says "Chart analysis is not
 * available right now" (never "Coming soon"), and shows the shell's standard "Coming soon" only when the flag is off.
 * They still leave the navigation while their flag is off.
 */
export const SELF_GATED: ReadonlySet<string> = new Set(["chart-analyze"]);

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
  if (!page || SELF_GATED.has(page)) return null;
  const flag = PAGE_FLAGS[page as keyof typeof PAGE_FLAGS];
  if (!flag || !flags.answered) return null;
  return flags.on(flag) ? null : flag;
}
