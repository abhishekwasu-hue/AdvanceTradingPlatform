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

/**
 * CP-UX CU4 (owner order 30 Oct, `ui_v2`): ONE Copilot - the chat (Ask) and the places the older tabs fold into.
 * M60 (owner order, approved 30 Oct with the five defaults): the tab row is Ask · Read the chart · Analyze a chart · Coach.
 * Read the chart = Market Pulse (the news part moved to Learn › News & events); Analyze a chart = the upload tool (its
 * own page /chart-analyze stays and is found by search); Coach = Review (the Coach tab) plus the Guide. Build left the
 * row (Strategies › Build is the one builder) and Copilot Signals opens the Signals page. The old addresses forward:
 * /copilot/brief -> /copilot/read, /copilot/review -> /copilot/coach, /copilot/signals -> /signals (shell/aliases.ts
 * V2_MOVED); their bodies stay here for any link that still reaches them. The seven older tabs keep their addresses
 * under "Advanced" (/copilot/coach is now the Coach tab).
 */
export const FOLDED_TABS = ["read", "coach", "brief", "review", "signals"] as const;
export type FoldedTab = (typeof FOLDED_TABS)[number];
export type CopilotV2Tab = "ask" | "read" | "analyze" | "coach";

/** Where Build goes: the existing strategy builder (Strategies › Build). */
export const BUILD_PATH = "/strategies/build";

/** M60 default 1: the Analyze a chart tab (the same tool as /chart-analyze), shown while `chart_analyze` is on. */
export const ANALYZE_TAB = "analyze";
export const ANALYZE_FLAG = "chart_analyze";

export const COPILOT_V2_TABS: readonly { key: CopilotV2Tab; to: string; flag?: string }[] = [
  { key: "ask", to: "/copilot" },
  { key: "read", to: "/copilot/read" },
  { key: "analyze", to: "/copilot/analyze", flag: ANALYZE_FLAG },
  { key: "coach", to: "/copilot/coach" },
];

/** The tab a folded body lights up (the old Brief is Read the chart, the old Review the Coach). */
export function v2TabOf(tab: FoldedTab | typeof ANALYZE_TAB | null): CopilotV2Tab {
  if (tab === null) return "ask";
  if (tab === "read" || tab === "brief") return "read";
  if (tab === "coach" || tab === "review") return "coach";
  if (tab === ANALYZE_TAB) return "analyze";
  return "ask";
}

export function isFoldedTab(v: string | undefined): v is FoldedTab {
  return !!v && (FOLDED_TABS as readonly string[]).includes(v);
}
