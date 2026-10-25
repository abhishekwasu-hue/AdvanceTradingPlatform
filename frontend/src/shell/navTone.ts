import { sectionTintOf, type SectionTint } from "../ui/accents";
import type { ShellKey } from "./navModel";

/**
 * Colour v2 (owner, 23 Oct: "group icons tinted per group, muted"): the colour of a navigation icon while it is not
 * the page on screen - one muted colour per group, so the sidebar, the bottom bar and the More sheet read as groups at
 * a glance. Broker is ATP blue, AI Copilot the AI violet (AI only), Markets the info sky, Trading teal, Insights a muted
 * magenta, Account and the rest neutral. The page on screen stays brand (the bottom bar adds its coloured pill).
 * Every colour is 3 : 1 on the sidebar, the bottom bar and the sheet's tiles (tokens.test.ts, "Colour v2 tokens").
 */
export type NavTone = "brand" | "ai" | "info" | "teal" | "insights" | "neutral";

const ICON: Record<NavTone, string> = {
  brand: "text-brand",
  ai: "text-ai",
  info: "text-info",
  teal: "text-teal-accent",
  insights: "text-nav-insights",
  neutral: "text-fg-muted",
};

/** UI-H CA3: read from THE section -> tint map (src/ui/accents.ts), so the plain icons and the chips agree. */
const FROM_TINT: Record<SectionTint, NavTone> = {
  markets: "info", trading: "teal", insights: "insights", account: "neutral", admin: "neutral", broker: "brand", copilot: "ai",
};

export function navTone(key: ShellKey | null): NavTone {
  const tint = sectionTintOf(key);
  return tint ? FROM_TINT[tint] : "neutral";
}

/** The icon's class: its group colour with colour v2 on, the neutral muted colour (as before) with it off. */
export function navIconClass(key: ShellKey | null, colour: boolean): string {
  return colour ? ICON[navTone(key)] : "text-fg-muted";
}
