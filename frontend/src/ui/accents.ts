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
export type SectionTint = "markets" | "trading" | "insights" | "account" | "admin" | "broker" | "copilot";

/** CA3: THE section -> tint map. The nav chips and the PageHeader chip both read it, so they can never disagree. */
export const SECTION_TINT: Record<SectionId, SectionTint> = {
  markets: "markets",
  strategies: "insights",
  trading: "trading",
  insights: "insights",
  account: "account",
  more: "account",
  admin: "admin",
};

/** Two entries keep their own colour whatever their section: Broker (ATP blue) and AI Copilot (the AI violet). */
const OWN_TINT: Partial<Record<ShellKey, SectionTint>> = { broker: "broker", copilot: "copilot" };

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
};

const CHIP_SIZE = { sm: "h-7 w-7", md: "h-9 w-9", lg: "h-10 w-10" } as const;

/** The round icon chip of a section: its soft tint behind the icon in the section's colour (4.5 : 1, both themes). */
export function sectionChipClass(tint: SectionTint, size: keyof typeof CHIP_SIZE = "sm"): string {
  return `flex shrink-0 items-center justify-center rounded-pill ${CHIP_SIZE[size]} ${CHIP[tint]}`;
}
