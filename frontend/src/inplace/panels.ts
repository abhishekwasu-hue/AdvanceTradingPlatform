import { BROKER_PATH } from "../broker/paths";
import { SETTINGS_PATH } from "../settings/paths";
import { chartTabPath, chartsSymbolFromPath } from "../shell/shellRoutes";
import { copilotToolPath, signalsPanelPath } from "./copilotPaths";
import { createRegistry, type PanelEntry, type PanelParams } from "./registry";

/**
 * NAV-1 (docs/design/M75_NO_NAVIGATION.md section 4.1): the first three in-place panels - a chart, the broker and the
 * AI key. Each reuses the existing component; it loads in its own chunk the first time the panel opens. The area PRs
 * (NAV-2..8) add their entries here. NAV-2: the Copilot, Analyze a chart, Strategies › Build, News & events, Signals.
 */

const clean = (pathname: string) => pathname.replace(/\/+$/, "") || "/";
const only = (o: Record<string, string | null | undefined>): PanelParams =>
  Object.fromEntries(Object.entries(o).filter((e): e is [string, string] => !!e[1]));

/** A chart: `/charts/NIFTY?tf=15min&exchange=BSE`, or the classic chart window's `?chart=NIFTY&tf=5min`. */
export const CHART_PANEL: PanelEntry = {
  kind: "chart",
  keys: ["symbol", "tf", "exchange"],
  title: (p) => p.symbol ?? "Chart",
  presentation: "side-wide",
  match: ({ pathname, search }) => {
    const fromPath = chartsSymbolFromPath(pathname);
    const symbol = fromPath ?? search.get("chart")?.trim().toUpperCase() ?? null;
    if (!symbol) return null;
    return only({ symbol, tf: search.get("tf"), exchange: search.get("exchange")?.trim().toUpperCase() });
  },
  href: (p) => chartTabPath(p.symbol ?? "", p.exchange, p.tf),
  load: () => import("./panels/ChartPanel"),
};

/** The broker: connect, renew or remove (the Broker page's own body). */
export const BROKER_PANEL: PanelEntry = {
  kind: "broker",
  keys: [],
  title: () => "Broker",
  presentation: "drawer",
  match: ({ pathname }) => (clean(pathname) === BROKER_PATH || clean(pathname) === "/settings/broker" ? {} : null),
  href: () => BROKER_PATH,
  load: () => import("./panels/BrokerPanel"),
};

/** The user's own AI key: Connect AI with `ai_one_key`; without it (NAV-2) what Settings › AI shows then. */
export const AI_KEY_PANEL: PanelEntry = {
  kind: "ai-key",
  keys: [],
  title: () => "Connect AI",
  presentation: "modal",
  match: ({ pathname }) => (clean(pathname) === SETTINGS_PATH.ai ? {} : null),
  href: () => SETTINGS_PATH.ai,
  load: () => import("./panels/AiKeyPanel"),
};

// ---- NAV-2: the Copilot area -------------------------------------------------------------------------------------

/** The Copilot's tools by their address (`/copilot/<tab>`): the seven older tools and the v2 Read the chart / Coach. */
const COPILOT_TOOLS = ["market-pulse", "strategy-lab", "idea-builder", "ask", "watchtower", "news-radar", "coach", "read"] as const;
const COPILOT_TITLE: Record<string, string> = {
  "market-pulse": "Market Pulse", "strategy-lab": "Strategy Lab", "idea-builder": "Idea Builder", ask: "Ask the Copilot",
  watchtower: "Watchtower", "news-radar": "News Radar", coach: "Coach", read: "Read the chart",
};
/** Old addresses the v2 shell forwards to a Copilot tool (shell/aliases.ts V2_MOVED); the Coach only while the Copilot
 * is on, which this panel needs anyway. */
const COPILOT_FORWARDED: Record<string, string> = {
  "/coach": "coach", "/analytics/coach": "coach", "/journal/coach": "coach", "/copilot/review": "coach", "/copilot/brief": "read",
};

/** The Copilot - the chat (`/copilot`, `?symbol=` from the Ask AI sheet) or one of its tools - beside the tab. Analyze
 * a chart has its own panel; `/copilot/signals` is the Signals page (its own route). */
export const COPILOT_PANEL: PanelEntry = {
  kind: "copilot",
  keys: ["tab", "symbol"],
  title: (p) => (p.tab ? COPILOT_TITLE[p.tab] ?? "Copilot" : "Copilot"),
  presentation: "side-wide",
  match: ({ pathname, search }) => {
    const path = clean(pathname);
    const forwarded = COPILOT_FORWARDED[path];
    if (forwarded) return { tab: forwarded };
    const m = /^\/copilot(?:\/([a-z-]+))?$/.exec(path);
    if (!m || (m[1] && !(COPILOT_TOOLS as readonly string[]).includes(m[1]))) return null;
    return only({ tab: m[1], symbol: search.get("symbol")?.trim().toUpperCase() });
  },
  href: (p) => copilotToolPath(p.tab, p.symbol),
  enabled: (on) => on("ai_copilot"),
  load: () => import("./panels/CopilotPanel"),
};

/** Analyze a chart image (`/chart-analyze`, the Copilot's `/copilot/analyze` tab), while `chart_analyze` is on. */
export const ANALYZE_PANEL: PanelEntry = {
  kind: "analyze",
  keys: [],
  title: () => "Analyze a chart",
  presentation: "side-wide",
  match: ({ pathname }) => (["/chart-analyze", "/copilot/analyze"].includes(clean(pathname)) ? {} : null),
  href: () => "/chart-analyze",
  enabled: (on) => on("chart_analyze"),
  load: () => import("./panels/AnalyzePanel"),
};

/** Strategies › Build (the old `/strategy-builder` too); the Copilot hands its idea over as before. */
export const STRATEGY_BUILD_PANEL: PanelEntry = {
  kind: "strategy-build",
  keys: [],
  title: () => "Build a strategy",
  presentation: "side-wide",
  match: ({ pathname }) => (["/strategies/build", "/strategy-builder"].includes(clean(pathname)) ? {} : null),
  href: () => "/strategies/build",
  load: () => import("./panels/StrategyBuildPanel"),
};

/** Learn › News & events (the old `/news-events` too). */
export const NEWS_PANEL: PanelEntry = {
  kind: "news",
  keys: [],
  title: () => "News & events",
  presentation: "drawer",
  match: ({ pathname }) => (["/learn/news", "/news-events"].includes(clean(pathname)) ? {} : null),
  href: () => "/learn/news",
  load: () => import("./panels/NewsPanel"),
};

/** The Signals list (`/signals`, a Watchtower proposal as `?proposal=`). */
export const SIGNALS_PANEL: PanelEntry = {
  kind: "signals",
  keys: ["proposal"],
  title: () => "Signals",
  presentation: "side",
  match: ({ pathname, search }) => (clean(pathname) === "/signals" ? only({ proposal: search.get("proposal")?.trim() }) : null),
  href: (p) => signalsPanelPath(p.proposal),
  load: () => import("./panels/SignalsPanel"),
};

export const DEFAULT_PANELS: readonly PanelEntry[] = [
  CHART_PANEL, BROKER_PANEL, AI_KEY_PANEL, COPILOT_PANEL, ANALYZE_PANEL, STRATEGY_BUILD_PANEL, NEWS_PANEL, SIGNALS_PANEL,
];
export const DEFAULT_REGISTRY = createRegistry(DEFAULT_PANELS);
