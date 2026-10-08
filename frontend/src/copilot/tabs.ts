/** The Copilot's seven tabs - each has its own address, /copilot/<slug>. */
export const COPILOT_TABS = ["market-pulse", "strategy-lab", "idea-builder", "ask", "watchtower", "news-radar", "coach"] as const;
export type CopilotTab = (typeof COPILOT_TABS)[number];
export const DEFAULT_TAB: CopilotTab = "market-pulse";

/** The addresses of the old page (/ai-copilot/<slug>) keep working: each opens the tab that now holds its content. */
export const LEGACY_TAB: Record<string, CopilotTab> = {
  study: "strategy-lab",
  today: "market-pulse",
  interview: "idea-builder",
  drafts: "strategy-lab",
  advanced: "watchtower",
};

export function isCopilotTab(v: string | undefined): v is CopilotTab {
  return !!v && (COPILOT_TABS as readonly string[]).includes(v);
}

export function copilotPath(tab: CopilotTab): string {
  return `/copilot/${tab}`;
}

/** The old page remembered its last tab by id (localStorage atp_copilot_tab). */
const LEGACY_STORED: Record<string, CopilotTab> = { strategist: "strategy-lab", today: "market-pulse", strategy: "idea-builder", advanced: "watchtower" };

/** /ai-copilot/<old slug> -> /copilot/<tab>. A bare /ai-copilot opens the tab the old page last showed (`stored`, its
 * remembered id), else the default tab. */
export function legacyCopilotPath(slug: string | undefined, stored?: string | null): string {
  return copilotPath((slug && LEGACY_TAB[slug]) || (isCopilotTab(slug) ? slug : (stored && LEGACY_STORED[stored]) || DEFAULT_TAB));
}

/** The server's Copilot router names a tab in its answer ("interview", "coach", ...): the tab that shows it. */
export function tabForAction(action: string): CopilotTab {
  const map: Record<string, CopilotTab> = {
    interview: "idea-builder", strategy: "idea-builder", coach: "coach", deployments: "market-pulse", brief: "market-pulse",
    today: "market-pulse", strategist: "strategy-lab", study: "strategy-lab", news: "news-radar", actions: "watchtower",
  };
  return map[action] ?? (isCopilotTab(action) ? action : DEFAULT_TAB);
}
