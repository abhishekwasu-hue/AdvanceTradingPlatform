import { BROKER_PATH } from "../broker/paths";
import { LEGAL_TITLES } from "../legal/legal";
import { SETTINGS_PATH } from "../settings/paths";
import { SCREEN_PARAM, screenIdOf, withScreen } from "../chart/screenParam";
import { chartTabPath, chartsSymbolFromPath } from "../shell/shellRoutes";
import { askAiPath, askContextFrom } from "../copilot/askContext";
import { copilotToolPath, signalsPanelPath } from "./copilotPaths";
import { OPTION_CHAIN_KEYS, futuresPanelPath, optionChainPanelPath } from "./fnoPaths";
import { createRegistry, type PanelEntry, type PanelParams, type Presentation } from "./registry";

/**
 * NAV-1 (docs/design/M75_NO_NAVIGATION.md section 4.1): the first three in-place panels - a chart, the broker and the
 * AI key. Each reuses the existing component; it loads in its own chunk the first time the panel opens. The area PRs
 * (NAV-2..8) add their entries here. NAV-2: the Copilot, Analyze a chart, Strategies › Build, News & events, Signals.
 * NAV-3: the Trading bot page. NAV-4: the Option chain, the symbol hub's Futures tab, Plan an options trade and Risk
 * limits. NAV-5a: Find setups, Strategies › Test, the journal, My positions, Trading defaults, Settings › Alerts (pages
 * shown whole by PagePanel, their search carried as one parameter); the Copilot's Watchtower proposal; the Trading bot's
 * template pre-fill. NAV-5b: Orders, Risk status and Set-up. NAV-6a: My money, Performance, Sectors and Custom scan.
 * NAV-6b: Notifications, Account & plan, the service status and the legal documents. NAV-7: Settings › Advanced,
 * Strategies, the Terminal and Today. NAV-8: an admin's view of one user's support status.
 */

const clean = (pathname: string) => pathname.replace(/\/+$/, "") || "/";
const only = (o: Record<string, string | null | undefined>): PanelParams =>
  Object.fromEntries(Object.entries(o).filter((e): e is [string, string] => !!e[1]));

/** NAV-5a: a page's whole search carried as one panel parameter (`q`), so a pre-fill in the address (a template's
 * settings, an idea's scan) reaches the page in the panel exactly as on its route. */
const withSearch = (home: string, q?: string) => (q ? `${home}?${q}` : home);
const searchOf = (search: URLSearchParams) => only({ q: search.toString() });

/** NAV-5a: a page shown whole in place (PagePanel), for the paths it answers for, while its flag (if any) is on. */
function pagePanel(o: { kind: string; title: string; presentation: Presentation; paths: readonly string[]; flag?: string;
  hash?: boolean }): PanelEntry {
  const home = o.paths[0];
  return {
    kind: o.kind,
    keys: o.hash ? ["q", "h"] : ["q"],
    title: () => o.title,
    presentation: o.presentation,
    match: ({ pathname, search, hash }) => (o.paths.includes(clean(pathname))
      ? only({ q: search.toString(), h: o.hash ? hash.replace(/^#/, "") : undefined }) : null),
    href: (p) => `${withSearch(home, p.q)}${p.h ? `#${p.h}` : ""}`,
    ...(o.flag ? { enabled: (on: (flag: string) => boolean) => on(o.flag as string) } : {}),
    load: () => import("./panels/PagePanel"),
    page: true,
  };
}

/** A chart: `/charts/NIFTY?tf=15min&exchange=BSE`, or the classic chart window's `?chart=NIFTY&tf=5min`. */
export const CHART_PANEL: PanelEntry = {
  kind: "chart",
  // M91 C1: `wscreen` - the watchlist's chosen screen the chart draws (absent: none; trailing, so older addresses read as before)
  keys: ["symbol", "tf", "exchange", "wscreen"],
  title: (p) => p.symbol ?? "Chart",
  presentation: "side-wide",
  match: ({ pathname, search }) => {
    const fromPath = chartsSymbolFromPath(pathname);
    const symbol = fromPath ?? search.get("chart")?.trim().toUpperCase() ?? null;
    if (!symbol) return null;
    return only({ symbol, tf: search.get("tf"), exchange: search.get("exchange")?.trim().toUpperCase(), wscreen: screenIdOf(search.get(SCREEN_PARAM)) });
  },
  href: (p) => withScreen(chartTabPath(p.symbol ?? "", p.exchange, p.tf), screenIdOf(p.wscreen) ? { screenId: Number(p.wscreen) } : null),
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
  watchtower: "Watchtower", "news-radar": "News Radar", coach: "Coach", read: "Market today",
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
  keys: ["tab", "symbol", "action", "tf", "ctx", "from"],
  title: (p) => (p.tab ? COPILOT_TITLE[p.tab] ?? "Copilot" : "Copilot"),
  presentation: "side-wide",
  phoneFull: true,              // owner 13 Nov: the chat as a full-height sheet on a phone, its input pinned
  match: ({ pathname, search }) => {
    const path = clean(pathname);
    const forwarded = COPILOT_FORWARDED[path];
    if (forwarded) return { tab: forwarded };
    const m = /^\/copilot(?:\/([a-z-]+))?$/.exec(path);
    if (!m || (m[1] && !(COPILOT_TOOLS as readonly string[]).includes(m[1]))) return null;
    // NAV-5a: Signals' "Open it in Watchtower" carries the proposal (`?action=`).
    // M72 PR-3: "Ask AI about this" - the chat's context (symbol, timeframe, one object, the asking page)
    const ctx = m[1] ? null : askContextFrom((k) => search.get(k));
    return only({ tab: m[1], symbol: search.get("symbol")?.trim().toUpperCase(), action: m[1] ? search.get("action")?.trim() : undefined,
      tf: ctx?.timeframe, ctx: ctx?.object ? `${ctx.object.kind}:${ctx.object.id}` : undefined, from: ctx?.page });
  },
  href: (p) => (p.tab ? copilotToolPath(p.tab, p.symbol, p.action) : askAiPath(askContextFrom((k) => p[k]) ?? {})),
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
  keys: ["q"],
  title: () => "Trading bot",
  presentation: "side-wide",
  // NAV-5a: a template picked by the address (`?template=D1&timeframe=1d&set.period=12`) rides along as `q`.
  match: ({ pathname, search }) => (clean(pathname) === "/deployments" ? searchOf(search) : null),
  href: (p) => withSearch("/deployments", p.q),
  load: () => import("./panels/BotsPanel"),
  page: true,
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
  page: true,
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
  page: true,
};

/** The trader's own Risk limits (`/settings/risk-limits`): the ticket's and the basket's "Change limit". (NAV-5b:
 * `/risk-management` is the Risk status page, its own panel below, not this one.) */
export const RISK_LIMITS_PANEL: PanelEntry = {
  kind: "risk-limits",
  keys: [],
  title: () => "Risk limits",
  presentation: "side",
  match: ({ pathname }) => (clean(pathname) === "/settings/risk-limits" ? {} : null),
  href: () => "/settings/risk-limits",
  load: () => import("./panels/RiskLimitsPanel"),
};

// ---- NAV-5a: Signals, bots, ideas, journal, scanner ------------------------------------------------------------------

/** Find setups (`/screener`; an idea's "Try as a scan" carries `?idea=&scan=`) while `screener_v2` is on. */
export const SCREENER_PANEL = pagePanel({ kind: "screener", title: "Find setups", presentation: "side-wide", paths: ["/screener"], flag: "screener_v2" });
/** Strategies › Test (`/strategies/test`, the old `/backtest`): "Test it first" with the template's settings. */
export const TEST_PANEL = pagePanel({ kind: "test", title: "Test a strategy", presentation: "side-wide", paths: ["/strategies/test", "/backtest"] });
/** The journal (`/positions/results/journal?week=...#trade-12`): a similar setup's entry, opened on its row. */
export const JOURNAL_PANEL = pagePanel({ kind: "journal", title: "Journal", presentation: "side-wide",
  paths: ["/positions/results/journal", "/analytics/journal", "/journal"], flag: "discipline_engine", hash: true });
/** My positions (`/positions`): a bot's open position chip. */
export const POSITIONS_PANEL = pagePanel({ kind: "positions", title: "My positions", presentation: "side-wide", paths: ["/positions"] });
/** Settings › My trading defaults: the Trading bot form's "change your defaults". */
export const DEFAULTS_PANEL = pagePanel({ kind: "trading-defaults", title: "My trading defaults", presentation: "side", paths: ["/settings/trading-defaults"] });
/** Settings › Alerts: a scan alert's "set up delivery". */
export const ALERTS_PANEL = pagePanel({ kind: "alerts", title: "Alerts", presentation: "side", paths: ["/settings/alerts"] });

// ---- NAV-5b: Positions, orders, portfolio, risk -------------------------------------------------------------------

/** My trades › Orders (`/positions/orders`): the closed trades' "Orders". */
export const ORDERS_PANEL = pagePanel({ kind: "orders", title: "Orders", presentation: "side-wide", paths: ["/positions/orders", "/orders"] });
/** Risk status (`/risk-management`: pauses, events, its own "Open Settings › Risk limits"): My money's "Risk limits". */
export const RISK_STATUS_PANEL = pagePanel({ kind: "risk", title: "Risk status", presentation: "side-wide", paths: ["/risk-management"] });
/** Settings › Set-up (`/settings/setup`): Risk's "change my set-up answers". */
export const SETUP_PANEL = pagePanel({ kind: "setup", title: "Set-up", presentation: "side-wide", paths: ["/settings/setup"] });

// ---- NAV-6a: Today cards and the attention list --------------------------------------------------------------------

/** My trades › My money (`/positions/money`, the old `/portfolio`): an attention item's "Portfolio". */
export const MONEY_PANEL = pagePanel({ kind: "money", title: "My money", presentation: "side-wide", paths: ["/positions/money", "/portfolio"] });
/** My trades › Results (`/positions/results`, the old `/analytics`): the Today card's "Performance". */
export const PERFORMANCE_PANEL = pagePanel({ kind: "performance", title: "Performance", presentation: "side-wide", paths: ["/positions/results", "/analytics"] });
/** Sectors & indices (`/markets?period=week`) while `markets_view` is on: the week tiles. */
export const MARKETS_PANEL = pagePanel({ kind: "markets", title: "Sectors & indices", presentation: "side-wide", paths: ["/markets"], flag: "markets_view" });
/** Custom scan (`/screener/custom`) while `screener_v2` is on: an empty signals card's "Set up a scan". */
export const CUSTOM_SCAN_PANEL = pagePanel({ kind: "custom-scan", title: "Custom scan", presentation: "side-wide", paths: ["/screener/custom"], flag: "screener_v2" });

// ---- NAV-6b: the shell (top bar, help, tours, notices, broker gate, status, legal banner) ----------------------------

/** Notifications (`/notifications`): the top bar's bell. */
export const NOTIFICATIONS_PANEL = pagePanel({ kind: "notifications", title: "Notifications", presentation: "drawer", paths: ["/notifications"] });
/** Account & plan (`/settings/account`, the old `/account`): the top bar's account notices. */
export const ACCOUNT_PANEL = pagePanel({ kind: "account", title: "Account & plan", presentation: "side", paths: ["/settings/account", "/account"] });

/** The service status (`/status`): the degraded notice's "See status", in a modal. */
export const STATUS_PANEL: PanelEntry = {
  kind: "status",
  keys: [],
  title: () => "Service status",
  presentation: "modal",
  match: ({ pathname }) => (clean(pathname) === "/status" ? {} : null),
  href: () => "/status",
  load: () => import("./panels/StatusPanel"),
};

/** A legal document (`/legal/<slug>`): the legal banner's document links, in a modal. */
export const LEGAL_PANEL: PanelEntry = {
  kind: "legal",
  keys: ["slug"],
  title: (p) => (p.slug && p.slug in LEGAL_TITLES ? LEGAL_TITLES[p.slug as keyof typeof LEGAL_TITLES] : "Legal"),
  presentation: "modal",
  match: ({ pathname }) => { const m = /^\/legal\/([a-z-]+)$/.exec(clean(pathname)); return m ? { slug: m[1] } : null; },
  href: (p) => `/legal/${p.slug ?? ""}`,
  load: () => import("./panels/LegalPanel"),
};

// ---- NAV-7: Settings and set-up -----------------------------------------------------------------------------------

/** Settings › Advanced (`/settings/advanced#go-live`, `#own-bot`, `#alert-delivery`): the section opens on that row. */
export const ADVANCED_PANEL = pagePanel({ kind: "advanced", title: "Advanced", presentation: "side-wide", paths: ["/settings/advanced"], hash: true });
/** Strategies (the library, `/strategies`): set-up's "browse the strategies". */
export const STRATEGIES_PANEL = pagePanel({ kind: "strategies", title: "Strategies", presentation: "side-wide", paths: ["/strategies"] });
/** The Terminal (`/terminal?view=classic`: Settings' "Open the previous Terminal") while `terminal` is on. */
export const TERMINAL_PANEL = pagePanel({ kind: "terminal", title: "Terminal", presentation: "side-wide", paths: ["/terminal"], flag: "terminal" });
/** Today (`/` with no search: the set-up wizard's "Go to Today"). An address on `/` with a search (the classic chart
 * window's `?chart=` / `?contract=`) is not Today. */
export const TODAY_PANEL: PanelEntry = {
  kind: "today",
  keys: [],
  title: () => "Today",
  presentation: "side-wide",
  match: ({ pathname, search, hash }) => (clean(pathname) === "/" && !search.toString() && !hash ? {} : null),
  href: () => "/",
  load: () => import("./panels/PagePanel"),
  page: true,
};

// ---- NAV-8: Admin ---------------------------------------------------------------------------------------------------

/** One user's support status (`/admin?section=support&user=12`): the user list's "Status". Only an address naming a
 * user is this panel; the Support section itself stays the console's own tab. */
export const ADMIN_SUPPORT_PANEL: PanelEntry = {
  kind: "admin-support",
  keys: ["user"],
  title: (p) => (p.user ? `Support · user ${p.user}` : "Support"),
  presentation: "side-wide",
  match: ({ pathname, search }) => {
    const user = search.get("user")?.trim() ?? "";
    return clean(pathname) === "/admin" && search.get("section") === "support" && /^\d{1,12}$/.test(user) ? { user } : null;
  },
  href: (p) => `/admin?section=support${p.user ? `&user=${p.user}` : ""}`,
  load: () => import("./panels/AdminSupportPanel"),
};

export const DEFAULT_PANELS: readonly PanelEntry[] = [
  FUTURES_PANEL, CHART_PANEL, BROKER_PANEL, AI_KEY_PANEL, COPILOT_PANEL, ANALYZE_PANEL, STRATEGY_BUILD_PANEL, NEWS_PANEL, SIGNALS_PANEL, BOTS_PANEL,
  OPTION_CHAIN_PANEL, OPTIONS_PLAN_PANEL, RISK_LIMITS_PANEL,
  SCREENER_PANEL, TEST_PANEL, JOURNAL_PANEL, POSITIONS_PANEL, DEFAULTS_PANEL, ALERTS_PANEL,
  ORDERS_PANEL, RISK_STATUS_PANEL, SETUP_PANEL,
  MONEY_PANEL, PERFORMANCE_PANEL, MARKETS_PANEL, CUSTOM_SCAN_PANEL,
  NOTIFICATIONS_PANEL, ACCOUNT_PANEL, STATUS_PANEL, LEGAL_PANEL,
  ADVANCED_PANEL, STRATEGIES_PANEL, TERMINAL_PANEL, TODAY_PANEL,
  ADMIN_SUPPORT_PANEL,
];
export const DEFAULT_REGISTRY = createRegistry(DEFAULT_PANELS);
