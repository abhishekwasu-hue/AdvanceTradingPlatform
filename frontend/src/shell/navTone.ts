import { sectionOf, type ShellKey } from "./navModel";

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

export function navTone(key: ShellKey | null): NavTone {
  if (key === "broker") return "brand";
  if (key === "copilot") return "ai";
  const section = sectionOf(key);
  if (section === "markets") return "info";
  if (section === "trading") return "teal";
  if (section === "insights") return "insights";
  return "neutral";
}

/** The icon's class: its group colour with colour v2 on, the neutral muted colour (as before) with it off. */
export function navIconClass(key: ShellKey | null, colour: boolean): string {
  return colour ? ICON[navTone(key)] : "text-fg-muted";
}
