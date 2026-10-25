import { sectionOf, type SectionId, type ShellKey } from "../shell/navModel";

/**
 * UI-H colour accents (owner order CA1-CA3, docs/owner-orders/ATP_ORDER_UI_H_CA.md). Project wide and permanent: the
 * ONLY place that names the blue -> violet gradient and the section identity tints. Every page, user and admin, gets
 * them through the shared components (Button `gradient`, NewBadge, PageHeader `section`, the nav chips, the Today hero
 * card); a page or feature file never writes these classes itself (accentScope.test.ts). All of it is shown only
 * while colour_v2 is on - the callers pass `useColourV2()`.
 */

/** CA2: the gradient fill under white text (AA at every point, accents.test.ts). */
export const GRADIENT_FILL = "bg-accent-gradient text-on-brand";

/** CA2: the gradient as a card's outline (the Today hero card), the card's own surface inside. */
export const HERO_CARD = "rounded-panel shadow-card accent-gradient-border";

/**
 * CA2: the area-of-interest highlight - token-ready. Nothing draws one outside the chart yet (the chart's zones are
 * canvas drawings, and charts never take the gradient); a future area-of-interest row or chip uses this class.
 */
export const AOI_HIGHLIGHT = "aoi-highlight rounded-control";

/** CA3: one identity tint per main navigation section (user and admin), plus Broker (pinned) and AI Copilot. */
export type SectionTint = "markets" | "trading" | "insights" | "account" | "admin" | "broker" | "copilot"
  // CA6-1 (owner, 25 Oct): the five bottom-bar destinations, five clearly different hues.
  | "today" | "charts" | "signals" | "trades" | "more";

/** CA3: THE section -> tint map. The nav chips and the PageHeader chip both read it, so they can never disagree. */
export const SECTION_TINT: Record<SectionId, SectionTint> = {
  markets: "markets",
  strategies: "insights",
  trading: "trading",
  insights: "insights",
  account: "account",
  more: "more",
  admin: "admin",
};

/**
 * Entries that keep their own colour whatever their section: Broker (ATP blue) and AI Copilot (the AI violet), and -
 * CA6-1 (owner, 25 Oct) - the four bottom-bar pages, so the bar shows four different hues (Today blue, Charts violet,
 * Signals amber, My trades pink; More is teal through its section). The sidebar, the More sheet and the PageHeader
 * read the same map, so a page has one colour everywhere.
 */
const OWN_TINT: Partial<Record<ShellKey, SectionTint>> = {
  broker: "broker", copilot: "copilot", dashboard: "today", charts: "charts", signals: "signals", positions: "trades",
};

export function sectionTintOf(key: ShellKey | null): SectionTint | null {
  if (key === null) return null;
  const own = OWN_TINT[key];
  if (own) return own;
  const section = sectionOf(key);
  return section ? SECTION_TINT[section] : null;
}

// Written out in full so Tailwind sees every class.
const CHIP: Record<SectionTint, string> = {
  markets: "bg-sec-markets-soft text-sec-markets",
  trading: "bg-sec-trading-soft text-sec-trading",
  insights: "bg-sec-insights-soft text-sec-insights",
  account: "bg-sec-account-soft text-sec-account",
  admin: "bg-sec-admin-soft text-sec-admin",
  broker: "bg-sec-broker-soft text-sec-broker",
  copilot: "bg-sec-copilot-soft text-sec-copilot",
  today: "bg-sec-today-soft text-sec-today",
  charts: "bg-sec-charts-soft text-sec-charts",
  signals: "bg-sec-signals-soft text-sec-signals",
  trades: "bg-sec-trades-soft text-sec-trades",
  more: "bg-sec-more-soft text-sec-more",
};

const CHIP_SIZE = { sm: "h-7 w-7", md: "h-9 w-9", lg: "h-10 w-10" } as const;

/** The round icon chip of a section: its soft tint behind the icon in the section's colour (4.5 : 1, both themes). */
export function sectionChipClass(tint: SectionTint, size: keyof typeof CHIP_SIZE = "sm"): string {
  return `flex shrink-0 items-center justify-center rounded-pill ${CHIP_SIZE[size]} ${CHIP[tint]}`;
}

// UI-H CA6 (owner, 25 Oct): the phone bottom bar. Every tab's icon sits on its section's soft round chip; the tab on
// screen takes the stronger tint - the solid section colour under the card colour - and its word in the section
// colour, bold. Written out in full so Tailwind sees every class (AA from the tokens, accents.test.tsx "CA6").
const NAV_CHIP_ACTIVE: Record<SectionTint, string> = {
  markets: "bg-sec-markets text-surface-1",
  trading: "bg-sec-trading text-surface-1",
  insights: "bg-sec-insights text-surface-1",
  account: "bg-sec-account text-surface-1",
  admin: "bg-sec-admin text-surface-1",
  broker: "bg-sec-broker text-surface-1",
  copilot: "bg-sec-copilot text-surface-1",
  today: "bg-sec-today text-surface-1",
  charts: "bg-sec-charts text-surface-1",
  signals: "bg-sec-signals text-surface-1",
  trades: "bg-sec-trades text-surface-1",
  more: "bg-sec-more text-surface-1",
};
const NAV_WORD: Record<SectionTint, string> = {
  markets: "text-sec-markets",
  trading: "text-sec-trading",
  insights: "text-sec-insights",
  account: "text-sec-account",
  admin: "text-sec-admin",
  broker: "text-sec-broker",
  copilot: "text-sec-copilot",
  today: "text-sec-today",
  charts: "text-sec-charts",
  signals: "text-sec-signals",
  trades: "text-sec-trades",
  more: "text-sec-more",
};

/** CA6: the bottom-bar tab's round chip - soft while another page is on screen, the strong tint while its own is. */
export function navChipClass(tint: SectionTint, active: boolean): string {
  return `flex h-9 w-9 items-center justify-center rounded-pill transition-colors duration-fast ${active ? NAV_CHIP_ACTIVE[tint] : CHIP[tint]}`;
}

/** CA6: the word of the bottom-bar tab on screen - its section colour (the caller adds the bold weight). */
export function navWordClass(tint: SectionTint): string {
  return NAV_WORD[tint];
}
