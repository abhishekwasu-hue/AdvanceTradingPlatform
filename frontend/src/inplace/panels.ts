import { BROKER_PATH } from "../broker/paths";
import { SETTINGS_PATH } from "../settings/paths";
import { chartTabPath, chartsSymbolFromPath } from "../shell/shellRoutes";
import { copilotToolPath, signalsPanelPath } from "./copilotPaths";
import { OPTION_CHAIN_KEYS, futuresPanelPath, optionChainPanelPath } from "./fnoPaths";
import { createRegistry, type PanelEntry, type PanelParams } from "./registry";

/**
 * NAV-1 (docs/design/M75_NO_NAVIGATION.md section 4.1): the first three in-place panels - a chart, the broker and the
 * AI key. Each reuses the existing component; it loads in its own chunk the first time the panel opens. The area PRs
 * (NAV-2..8) add their entries here. NAV-2: the Copilot, Analyze a chart, Strategies › Build, News & events, Signals.
 * NAV-3: the Trading bot page. NAV-4: the Option chain, the symbol hub's Futures tab, Plan an options trade and Risk
 * limits.
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

// ---- NAV-3: Terminal / charts ------------------------------------------------------------------------------------

/** The Trading bot page (`/deployments`): start a bot, your bots as cards. A Copilot draft handed over with the address
 * opens in it as on the page (its own location carries the state). A wide side panel, not a drawer: the page lays its
 * form out by the screen's width, and in a drawer's 448 px at 1440 its "Discard the draft" button ran past the edge. */
export const BOTS_PANEL: PanelEntry = {
  kind: "bots",
  keys: [],
  title: () => "Trading bot",
  presentation: "side-wide",
  match: ({ pathname }) => (clean(pathname) === "/deployments" ? {} : null),
  href: () => "/deployments",
  load: () => import("./panels/BotsPanel"),
};

// ---- NAV-4: Option chain (F&O) ------------------------------------------------------------------------------------

/** The Option chain (`/option-chain?underlying=NIFTY&...`): a signal's strikes, the futures chain's basket badge
 * (`basket=open`), "Trade this setup" (`trade=1`) and a position's Adjust open it over the tab they are on. */
export const OPTION_CHAIN_PANEL: PanelEntry = {
  kind: "option-chain",
  keys: OPTION_CHAIN_KEYS,
  title: (p) => (p.underlying ? `Option chain · ${p.underlying}` : "Option chain"),
  presentation: "side-wide",
  match: ({ pathname, search }) => (clean(pathname) === "/option-chain"
    ? only(Object.fromEntries(OPTION_CHAIN_KEYS.map((k) => [k, search.get(k)?.trim()]))) : null),
  href: (p) => optionChainPanelPath(p),
  load: () => import("./panels/OptionChainPanel"),
};

/** The symbol hub's Futures tab (`/charts/<symbol>?hub=futures`, a future position's Adjust). Listed before the chart
 * panel, which answers for the same path without `hub`. */
export const FUTURES_PANEL: PanelEntry = {
  kind: "futures",
  keys: ["symbol", "tf", "exchange", "expiry"],
  title: (p) => (p.symbol ? `Futures · ${p.symbol}` : "Futures"),
  presentation: "side-wide",
  match: ({ pathname, search }) => {
    const symbol = chartsSymbolFromPath(pathname);
    if (!symbol || search.get("hub") !== "futures") return null;
    return only({ symbol, tf: search.get("tf"), exchange: search.get("exchange")?.trim().toUpperCase(), expiry: search.get("expiry")?.trim() });
  },
  href: (p) => futuresPanelPath(p),
  load: () => import("./panels/FuturesPanel"),
};

/** Plan an options trade (the Options Builder, `/option-chain/plan`, the old `/options-builder`) while `options_builder`
 * is on: the basket's "Open in Options Builder" opens it beside the basket. */
export const OPTIONS_PLAN_PANEL: PanelEntry = {
  kind: "options-plan",
  keys: [],
  title: () => "Plan an options trade",
  presentation: "side-wide",
  match: ({ pathname }) => (["/option-chain/plan", "/options-builder"].includes(clean(pathname)) ? {} : null),
  href: () => "/option-chain/plan",
  enabled: (on) => on("options_builder"),
  load: () => import("./panels/OptionsPlanPanel"),
};

/** The trader's own Risk limits (`/settings/risk-limits`, the old `/risk-management`): the ticket's and the basket's
 * "Change limit". */
export const RISK_LIMITS_PANEL: PanelEntry = {
  kind: "risk-limits",
  keys: [],
  title: () => "Risk limits",
  presentation: "side",
  match: ({ pathname }) => (["/settings/risk-limits", "/risk-management"].includes(clean(pathname)) ? {} : null),
  href: () => "/settings/risk-limits",
  load: () => import("./panels/RiskLimitsPanel"),
};

export const DEFAULT_PANELS: readonly PanelEntry[] = [
  FUTURES_PANEL, CHART_PANEL, BROKER_PANEL, AI_KEY_PANEL, COPILOT_PANEL, ANALYZE_PANEL, STRATEGY_BUILD_PANEL, NEWS_PANEL, SIGNALS_PANEL, BOTS_PANEL,
  OPTION_CHAIN_PANEL, OPTIONS_PLAN_PANEL, RISK_LIMITS_PANEL,
];
export const DEFAULT_REGISTRY = createRegistry(DEFAULT_PANELS);
