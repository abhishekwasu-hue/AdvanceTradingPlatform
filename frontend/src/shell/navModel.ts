import {
  BarChart3, Bell, BookOpen, Bot, Box, Building2, ChartCandlestick, ClipboardList, Eye, Filter, GraduationCap, History, Layers,
  Inbox, LayoutDashboard, LayoutGrid, Lightbulb, Link2, ListChecks, ListTodo, MonitorDot, Newspaper, Pencil, PieChart, Radar, ScanEye, ScrollText, Settings, Shield, ShieldEllipsis,
  ScanSearch, Sigma, SlidersHorizontal, Sparkles, Store, UserCircle2, Users, Zap, ChartSpline, Plug, Palette, LifeBuoy, Wrench, type LucideIcon,
} from "lucide-react";
import { needsAttention, type BrokerDot } from "../broker/brokerState";
import type { Page } from "../components/Sidebar";
import { PAGE_FLAGS } from "./pageFlags";
import { SETTINGS_PATH, type SettingsSectionKey } from "../settings/paths";
import { IDEA_FLAG } from "../ideas/model";
import { LEARN_ADDRESS } from "../learn/model";
import { TERMINAL_ADDRESS, WATCHLIST_ADDRESS } from "./aliases";

/**
 * UI2-1 (UI v2 proposal §2 navigation map, §4 bottom bar): the one navigation model the v2 shell reads - the sidebar,
 * the phone bottom bar, the More sheet and the command bar all list pages from here, so they can never disagree.
 *
 * Visibility (§2): "all" items show for every signed-in role; an item with `flag` is hidden while that feature flag
 * is off (or not known yet - fail closed); an item with `role` shows for that role only (Admin console: SUPER_ADMIN).
 * Pages whose feature is off are hidden, never shown disabled (§9 rule 9).
 */

/** A v2 shell location: every routed page, plus the v2-only pages: Charts (`/charts`, `/charts/:symbol`) and
 * Settings → Setup (`/settings/setup`, UI2-7), My trading defaults (`/settings/trading-defaults`, part C) and the Settings section pages (`/settings/ai` ..., Settings redesign B).
 * M60: Learn › Words explained (`/learn/words`) and two AI Copilot tabs that search lists on their own (Read the chart, Coach). */
export type ShellKey = Page | "charts" | "setup" | "trading-defaults" | "ideas" | "markets" | "learn" | "terminal" | "watchlist" | SettingsSectionKey
  | "learn-words" | "copilot-read" | "copilot-coach";

/** M60 (owner order, approved 30 Oct): the five sidebar sections; `more` only captions search results. */
export type SectionId = "trade" | "find" | "build" | "learn" | "account" | "more";

export interface NavItemV2 {
  key: ShellKey;
  /** The sidebar label (§2 "v2 sidebar label"). */
  label: string;
  /** The page title, the one question the page answers (§2 "v2 page title"). */
  title: string;
  path: string;
  icon: LucideIcon;
  /** Hidden while this feature flag is off for the organisation. */
  flag?: string;
  /** Shown to this role only. */
  role?: string;
  /** Other words a trader may type to find it: today's labels, so a renamed page is still found (§3 renames). */
  keywords?: string[];
  /** M60: listed in the sidebar only while this flag is off (its new home behind the flag is not on); always searchable. */
  listedUnless?: string;
  /** M60: hidden everywhere (sidebar and search) while this flag is on - another entry then holds the same page. */
  offWhen?: string;
  /** M60: the bottom bar's word when the label does not fit a fifth of a 360 px phone on one line. */
  short?: string;
}

export interface NavSectionV2 {
  id: SectionId;
  title: string;
  items: NavItemV2[];
}

const item = (key: ShellKey, label: string, title: string, path: string, icon: LucideIcon, extra: Partial<NavItemV2> = {}): NavItemV2 =>
  ({ key, label, title, path, icon, ...extra });

/** M60 default 4: the Admin console, at the end of ACCOUNT, for the platform operator only (the server stays the gate). */
export const ADMIN_ROLE = "SUPER_ADMIN";

/** M60: Learn › News & events, Learn › Ideas and Learn › Words explained (the old /news-events and /ideas forward here). */
export const LEARN_NEWS_ADDRESS = "/learn/news";
export const LEARN_IDEAS_ADDRESS = "/learn/ideas";
export const LEARN_WORDS_ADDRESS = "/learn/words";
/** M60: My trades › Results (today's Performance) and its Journal part (the old /analytics and /journal forward here). */
export const RESULTS_ADDRESS = "/positions/results";
export const RESULTS_JOURNAL_ADDRESS = "/positions/results/journal";
/** M60: Settings › Account & broker › Broker accounts (the old /broker forwards here). */
export const SETTINGS_BROKER_ADDRESS = "/settings/broker";

/**
 * M60 (owner order, approved 30 Oct with the five defaults; the before / after table in docs/ui/M60_SIDEBAR.html): one
 * feature, one home - 11 user entries in five short sections, each with its icon and one accent (src/ui/accents.ts), and
 * Admin at the end of ACCOUNT for the platform operator only. The phone bottom bar is the first four entries a trader
 * may see, plus More.
 *
 * - TRADE: Today, Terminal (chart, option chain and order in one screen), My trades (Positions · Orders · Money · Results).
 *   While `terminal` is off, Charts and Option chain stand in its place (`listedUnless`), so neither leaves the menu.
 * - FIND: Find setups, Signals, Sectors & indices.  BUILD: Strategies (Library · Build · Test), AI Trading Bot.
 * - LEARN & AI: AI Copilot (Ask · Read the chart · Analyze a chart · Coach), Learn (Lessons · Words explained · Ideas ·
 *   News & events). While `learn` is off, News & events stands in for Learn.
 * - ACCOUNT: Settings (Broker accounts inside, with the broker's connection dot on this row), Admin (super-admin only).
 *
 * Every other page keeps its address and is found by search (NAV_EXTRAS_V2); a moved page's old address forwards to its
 * new home (shellRoutes.ts v2RedirectFor), so nothing a trader uses today becomes unreachable.
 */
export const NAV_SECTIONS_V2: readonly NavSectionV2[] = [
  {
    id: "trade", title: "Trade", items: [
      item("dashboard", "Today", "Today", "/", LayoutDashboard, { keywords: ["home", "overview", "dashboard"] }),
      // Trading Terminal T1 (TT-18): the chart, the option chain and the order in one screen, while `terminal` is on.
      item("terminal", "Terminal", "Terminal", TERMINAL_ADDRESS, MonitorDot,
        { flag: PAGE_FLAGS.terminal, keywords: ["trading terminal", "trade screen", "workspace", "order panel", "scalper", "option seller"] }),
      item("charts", "Charts", "Charts", "/charts", ChartCandlestick, { listedUnless: PAGE_FLAGS.terminal, keywords: ["chart", "candles", "price"] }),
      item("option-chain", "Option chain", "Option chain", "/option-chain", Link2,
        { listedUnless: PAGE_FLAGS.terminal, short: "Chain", keywords: ["options", "oi", "pcr", "max pain"] }),
      item("positions", "My trades", "My trades", "/positions", ListChecks, { keywords: ["my positions", "positions", "holdings", "open trades", "closed trades"] }),
    ],
  },
  {
    id: "find", title: "Find", items: [
      item("screener", "Find setups", "Find setups", "/screener", Filter, { flag: PAGE_FLAGS.screener, keywords: ["screener", "screens", "scan"] }),
      item("signals", "Signals", "Signals", "/signals", Zap, { keywords: ["approvals", "alerts", "watchtower", "copilot signals"] }),
      // MO4 slice 2: the Sector & index view (v2 only, flag `markets_view`).
      item("markets", "Sectors & indices", "Sectors & indices", "/markets", LayoutGrid,
        { flag: PAGE_FLAGS.markets, keywords: ["sectors", "indices", "heat map", "heatmap", "sector rotation", "breadth", "market"] }),
    ],
  },
  {
    id: "build", title: "Build", items: [
      item("strategies", "Strategies", "Strategies", "/strategies", Layers, { keywords: ["strategy library", "library"] }),
      // M60: the new sidebar label; the page keeps its own title ("Trading bot", the page is another session's).
      item("deployments", "AI Trading Bot", "Trading bot", "/deployments", Bot,
        { keywords: ["trading bot", "autopilot", "deployments", "deploy", "running", "bots"] }),
    ],
  },
  {
    id: "learn", title: "Learn & AI", items: [
      item("copilot", "AI Copilot", "AI Copilot", "/copilot", Sparkles, { flag: "ai_copilot", keywords: ["ai", "assistant", "ask"] }),
      // Readiness C13 (flag `learn`): short lessons, the glossary, the idea library and News & events (education only).
      item("learn", "Learn", "Learn", LEARN_ADDRESS, GraduationCap,
        { flag: PAGE_FLAGS.learn, keywords: ["lessons", "course", "videos", "basics", "beginner", "how to"] }),
      item("news-events", "News & events", "News & events", LEARN_NEWS_ADDRESS, Newspaper,
        { listedUnless: PAGE_FLAGS.learn, keywords: ["news", "events", "calendar", "news radar"] }),
    ],
  },
  {
    id: "account", title: "Account", items: [
      item("settings", "Settings", "Settings", "/settings", Settings,
        { keywords: ["preferences", "appearance", "theme", "integrations", "webhook"] }),
      item("admin", "Admin", "Admin console", "/admin", ShieldEllipsis, { role: ADMIN_ROLE, keywords: ["admin console", "platform"] }),
    ],
  },
];

/** M60: no entry sits above the sections any more - Broker accounts live in Settings (the dot moved to the Settings row). */
export const NAV_PINNED_V2: readonly (NavItemV2 & { section: SectionId })[] = [];

/** The pinned entries this trader may see (none since M60). */
export function visiblePinned(ctx: NavContext): NavItemV2[] {
  return NAV_PINNED_V2.filter((i) => isItemVisible(i, ctx));
}

/** M60: the entry that carries the broker's status dot - Settings, the home of Broker accounts. */
export const DOT_ENTRY: ShellKey = "settings";

/** The dot an entry carries: the broker's status on Settings and on its Broker accounts page (search), nothing elsewhere. */
export function navDotFor(key: ShellKey, dot: BrokerDot | null): BrokerDot | null {
  return key === DOT_ENTRY || key === "broker" ? dot : null;
}

/** The phone's More button carries the dot too while a broker needs attention (amber or red). */
export function moreButtonDot(dot: BrokerDot | null): BrokerDot | null {
  return needsAttention(dot) ? dot : null;
}

/**
 * Pages that keep working but have no sidebar entry: the tabs of a merged page (at their new addresses; the old
 * addresses forward there), the pages of Settings, and the rarely used tools taken out of the user navigation. They are
 * found through search (the command bar, the More sheet and the Settings index), so nothing becomes unreachable.
 */
export const NAV_EXTRAS_V2: readonly (NavItemV2 & { section: SectionId })[] = [
  // M60: parts of the Terminal today (/watchlist keeps its page until the Terminal has a Watchlist panel, UX-3).
  { ...item("watchlist", "Watchlist", "Watchlist", WATCHLIST_ADDRESS, Eye,
    { flag: PAGE_FLAGS.watchlist, keywords: ["watchlists", "watch list", "my lists", "indices", "index stocks", "sectors", "f&o", "prices"] }), section: "trade" },
  { ...item("options-builder", "Plan an options trade", "Plan an options trade", "/option-chain/plan", ChartSpline,
    { flag: "options_builder", keywords: ["options builder", "payoff", "strategy"] }), section: "trade" },
  // My trades' tabs (the old /orders, /portfolio, /analytics and /journal forward here). Results = today's Performance.
  { ...item("orders", "Orders", "My trades", "/positions/orders", ClipboardList, { keywords: ["orders and fills", "fills", "trade history", "blotter"] }), section: "trade" },
  { ...item("portfolio", "Money", "My trades", "/positions/money", PieChart, { keywords: ["portfolio", "my portfolio", "exposure", "where my money is"] }), section: "trade" },
  { ...item("analytics", "Results", "How am I doing?", RESULTS_ADDRESS, BarChart3, { keywords: ["performance", "analytics", "p&l", "win rate"] }), section: "trade" },
  { ...item("journal", "Journal", "How am I doing?", RESULTS_JOURNAL_ADDRESS, BookOpen, { flag: "discipline_engine", keywords: ["journal and discipline", "discipline"] }), section: "trade" },
  // M60 default 3: the Coach is an AI Copilot tab; while the Copilot is off it stays a part of My trades › Results.
  { ...item("coach", "Coach", "How am I doing?", "/analytics/coach", GraduationCap,
    { offWhen: "ai_copilot", keywords: ["coach and guide", "trade coach", "guide", "review"] }), section: "trade" },
  // Find setups' own sub-page (the old /scanner forwards here).
  { ...item("scanner", "Custom scan", "Custom scan", "/screener/custom", Radar, { keywords: ["market scanner", "scanner", "scan"] }), section: "find" },
  // Strategies' tabs (the old /strategy-builder and /backtest forward here).
  { ...item("strategy-builder", "Build a strategy", "Strategies", "/strategies/build", Pencil, { keywords: ["strategy builder", "create", "build"] }), section: "build" },
  { ...item("backtest", "Test a strategy", "Strategies", "/strategies/test", History, { keywords: ["backtest", "backtesting", "test"] }), section: "build" },
  // M60 defaults 1 and 3: two AI Copilot tabs found on their own, and the standalone Analyze a chart page (kept).
  { ...item("copilot-read", "Read the chart", "AI Copilot", "/copilot/read", ScanEye,
    { flag: "ai_copilot", keywords: ["brief", "market pulse", "copilot brief"] }), section: "learn" },
  { ...item("copilot-coach", "Coach", "AI Copilot", "/copilot/coach", GraduationCap,
    { flag: "ai_copilot", keywords: ["coach and guide", "trade coach", "guide", "review", "copilot review"] }), section: "learn" },
  { ...item("chart-analyze", "Analyze a chart", "Analyze a chart", "/chart-analyze", ScanSearch,
    { flag: "chart_analyze", keywords: ["analyze chart", "analyse chart", "chart reading", "screenshot", "tradingview", "upload"] }), section: "learn" },
  // Learn's tabs. Idea library slice 1 (flag idea_library): method ideas in plain words (education only).
  { ...item("learn-words", "Words explained", "Learn", LEARN_WORDS_ADDRESS, BookOpen,
    { flag: PAGE_FLAGS.learn, keywords: ["glossary", "words", "terms", "meaning"] }), section: "learn" },
  { ...item("ideas", "Ideas", "Learn", LEARN_IDEAS_ADDRESS, Lightbulb, { flag: IDEA_FLAG, keywords: ["idea library", "ideas", "learn", "methods", "price action"] }), section: "learn" },
  { ...item("fundamentals", "Company research", "Company research", "/fundamentals", Building2, { keywords: ["fundamental analysis", "fundamentals"] }), section: "more" },
  // Settings' pages, in its four tabs (Account & broker · Trading · AI · App); the Settings index lists them too.
  { ...item("account", "Account & plan", "Account", "/settings/account", UserCircle2, { keywords: ["account", "profile", "password", "two-factor", "sessions", "email", "plan"] }), section: "account" },
  { ...item("broker", "Broker accounts", "Broker", SETTINGS_BROKER_ADDRESS, Plug, { keywords: ["broker", "brokers", "connect broker", "renew", "login", "upstox", "zerodha"] }), section: "account" },
  { ...item("team", "Team", "Team", "/team", Users, { keywords: ["members", "invite"] }), section: "account" },
  // UI2-7: Settings → Setup, the setup wizard and the go-live checklist (§11).
  { ...item("setup", "Setup", "Setup", "/settings/setup", ListTodo, { keywords: ["set up your account", "setup wizard", "get started", "go-live checklist", "readiness"] }), section: "account" },
  // Settings redesign part C: the user's own starting values for new bots and chain tickets (a pre-fill, never a rule).
  { ...item("trading-defaults", "My trading defaults", "My trading defaults", "/settings/trading-defaults", SlidersHorizontal,
    { keywords: ["defaults", "default lots", "presets", "order presets", "expiry rule", "strike rule", "trailing stop"] }), section: "account" },
  { ...item("risk-management", "Risk limits", "Risk limits", "/risk-management", Shield, { keywords: ["risk", "risk management", "limits", "daily loss"] }), section: "account" },
  // M60 default 5: the alert channels in Settings › Trading, with the Inbox (Notifications) beside them.
  { ...item("settings-alerts", "Alerts", "Alerts", SETTINGS_PATH.alerts, Bell, { keywords: ["telegram", "alert channels", "email alerts", "sms", "push notifications"] }), section: "account" },
  { ...item("notifications", "Inbox", "Notifications", "/notifications", Inbox, { keywords: ["notifications", "alerts", "inbox"] }), section: "account" },
  { ...item("settings-ai", "AI", "AI", SETTINGS_PATH.ai, Sparkles, { keywords: ["ai key", "api key", "connect ai", "claude", "openai", "gemini", "ai limits"] }), section: "account" },
  { ...item("settings-appearance", "Appearance", "Appearance", SETTINGS_PATH.appearance, Palette, { keywords: ["theme", "dark mode", "light mode", "text size", "colour-blind", "reduce motion"] }), section: "account" },
  { ...item("settings-help", "Help & tips", "Help & tips", SETTINGS_PATH.help, LifeBuoy, { keywords: ["tips", "page tips", "tours", "show get started", "help centre", "help center", "faq", "report a problem", "contact support"] }), section: "account" },
  { ...item("settings-advanced", "Advanced", "Advanced", SETTINGS_PATH.advanced, Wrench, { keywords: ["webhook", "tradingview", "own telegram bot", "integrations"] }), section: "account" },
  // Audit section 5 "Drop from the user navigation": still there, found by search.
  { ...item("quant", "Factor Lab", "Factor Lab", "/quant", Sigma, { keywords: ["quant", "factors"] }), section: "more" },
  { ...item("instruments", "Contract specs", "Contract specs", "/instruments", Box, { keywords: ["instruments", "lot size", "expiry"] }), section: "more" },
  { ...item("marketplace", "Marketplace", "Strategy marketplace", "/marketplace", Store, { flag: "marketplace", keywords: ["marketplace"] }), section: "more" },
  { ...item("system-logs", "Activity log", "Your account activity", "/system-logs", ScrollText, { keywords: ["system logs", "audit"] }), section: "more" },
];

/**
 * The sidebar entry a page belongs to - a merged page's tab lights up its entry (Orders -> My trades), a Settings page
 * lights up Settings, a part of the Terminal the Terminal. Null: none. An entry whose own key is on screen is marked too
 * (isCurrentEntry: Charts while it stands in for the Terminal).
 */
const NAV_HOME: Partial<Record<ShellKey, ShellKey>> = {
  orders: "positions", portfolio: "positions", analytics: "positions", journal: "positions", coach: "positions",
  charts: "terminal", "option-chain": "terminal", watchlist: "terminal", "options-builder": "terminal",
  scanner: "screener",
  "strategy-builder": "strategies", backtest: "strategies",
  "copilot-read": "copilot", "copilot-coach": "copilot", "chart-analyze": "copilot",
  "learn-words": "learn", ideas: "learn", "news-events": "learn",
  broker: "settings", "risk-management": "settings", team: "settings", account: "settings", setup: "settings", "trading-defaults": "settings",
  notifications: "settings", "settings-ai": "settings", "settings-alerts": "settings", "settings-appearance": "settings",
  "settings-help": "settings", "settings-advanced": "settings",
};

/** The sidebar / bottom bar entry that shows as current for the page on screen. */
export function navEntryFor(key: ShellKey | null): ShellKey | null {
  if (key === null) return null;
  return NAV_HOME[key] ?? key;
}

/** True when the entry `itemKey` is the page on screen or that page's home (`current`: the page's own key). */
export function isCurrentEntry(itemKey: ShellKey, current: ShellKey | null): boolean {
  return current !== null && (itemKey === current || itemKey === navEntryFor(current));
}

/** M60: the phone bottom bar - the first four entries this trader may see in the sidebar (More is the fifth button). */
export const BOTTOM_BAR_SIZE = 4;

/** The bottom bar's entries this trader may see (no context: no flag on, an ordinary trader). */
export function visibleBottomBar(ctx?: NavContext | Pick<NavContext, "on">): { key: ShellKey; label: string }[] {
  const full: NavContext = { on: ctx?.on ?? (() => false), role: ctx && "role" in ctx ? ctx.role : null };
  return visibleSections(full).flatMap((s) => s.items).filter((i) => !i.role).slice(0, BOTTOM_BAR_SIZE)
    .map(({ key, label, short }) => ({ key, label: short ?? label }));
}

/** Who is looking and which flags are on - all the navigation needs to decide what to list. */
export interface NavContext {
  /** True only when the flag is known and on (useFeatures().on). */
  on: (flag: string) => boolean;
  role: string | null;
}

export function isItemVisible(it: Pick<NavItemV2, "flag" | "role" | "offWhen">, ctx: NavContext): boolean {
  if (it.flag && !ctx.on(it.flag)) return false;
  if (it.role && ctx.role !== it.role) return false;
  if (it.offWhen && ctx.on(it.offWhen)) return false;
  return true;
}

/** Listed in the sidebar: visible, and not a stand-in whose new home is on (M60 `listedUnless`). */
export function isItemListed(it: NavItemV2, ctx: NavContext): boolean {
  return isItemVisible(it, ctx) && !(it.listedUnless && ctx.on(it.listedUnless));
}

/** The sections with only the items this trader may see; a section left empty is dropped. */
export function visibleSections(ctx: NavContext, sections: readonly NavSectionV2[] = NAV_SECTIONS_V2): NavSectionV2[] {
  return sections
    .map((s) => ({ ...s, items: s.items.filter((i) => isItemListed(i, ctx)) }))
    .filter((s) => s.items.length > 0);
}

/** Every page in the navigation, extras included, in order - for the command bar and the More sheet's search. */
export function allNavItems(): (NavItemV2 & { section: SectionId })[] {
  const main = NAV_SECTIONS_V2.flatMap((s) => s.items.map((i) => ({ ...i, section: s.id })));
  return [...NAV_PINNED_V2, ...main, ...NAV_EXTRAS_V2];
}

/** The navigation entry for a shell location (sidebar items first, then the extras). */
export function navItemFor(key: ShellKey | null): (NavItemV2 & { section: SectionId }) | null {
  if (key === null) return null;
  return allNavItems().find((i) => i.key === key) ?? null;
}

/** The section that holds a location (search captions, the section accent of its page). */
export function sectionOf(key: ShellKey | null): SectionId | null {
  return navItemFor(key)?.section ?? null;
}

/** P2b: the More sheet's first tiles, above the sections: the pinned entries (none since M60). */
export function moreSheetPinned(ctx: NavContext): NavItemV2[] {
  const inBar = new Set<ShellKey>(visibleBottomBar(ctx).map((b) => b.key));
  return visiblePinned(ctx).filter((i) => !inBar.has(i.key));
}

/** The More sheet (§4): every listed page that is not in the bottom bar, grouped by the same sections. M57 U7-2: `all`
 * (the menu button in the top bar) lists every section, the bar's entries too. */
export function moreSheetSections(ctx: NavContext, all = false): NavSectionV2[] {
  const inBar = new Set<ShellKey>(all ? [] : visibleBottomBar(ctx).map((b) => b.key));
  return visibleSections(ctx)
    .map((s) => ({ ...s, items: s.items.filter((i) => !inBar.has(i.key)) }))
    .filter((s) => s.items.length > 0);
}

/** The caption of each group (sidebar, More sheet, search results); "More" only captions search results. */
const SECTION_TITLES: Record<SectionId, string> = {
  trade: "Trade", find: "Find", build: "Build", learn: "Learn & AI", account: "Account", more: "More",
};
export const sectionTitle = (id: SectionId): string => SECTION_TITLES[id];

const norm = (s: string) => s.toLowerCase().normalize("NFKD").replace(/[^\p{L}\p{N}&?]+/gu, " ").trim();

/** What search reads from an entry: a page (allNavItems) or, on the Settings index, a setting inside a section. */
export type Searchable = Pick<NavItemV2, "label" | "title" | "keywords" | "flag" | "role" | "offWhen"> & { section?: SectionId };

/**
 * Pages matching what the trader typed, best first: the label starting with the query, then a word of the label or
 * title starting with it, then the label or title containing it, then a keyword (today's name) matching it. Every
 * word typed must match somewhere. Hidden pages (flag off, other role) are never returned.
 */
export function searchPages(query: string, ctx: NavContext): (NavItemV2 & { section: SectionId })[];
export function searchPages<T extends Searchable>(query: string, ctx: NavContext, items: readonly T[]): T[];
export function searchPages(query: string, ctx: NavContext, items: readonly Searchable[] = allNavItems()): Searchable[] {
  const q = norm(query);
  const visible = items.filter((i) => isItemVisible(i, ctx));
  if (!q) return visible;
  const words = q.split(" ");
  const scored: { it: Searchable; score: number; at: number }[] = [];
  visible.forEach((it, at) => {
    const label = norm(it.label);
    const title = norm(it.title);
    const keys = (it.keywords ?? []).map(norm);
    const haystack = [label, title, ...keys, it.section ? norm(sectionTitle(it.section)) : ""].join(" | ");
    if (!words.every((w) => haystack.includes(w))) return;
    const wordStart = (s: string) => s.split(" ").some((w) => w.startsWith(q)) || s.startsWith(q);
    let score: number;
    if (label.startsWith(q)) score = 0;
    else if (wordStart(label) || wordStart(title)) score = 1;
    else if (label.includes(q) || title.includes(q)) score = 2;
    else if (keys.some((k) => k.startsWith(q) || wordStart(k))) score = 3;
    else score = 4;
    scored.push({ it, score, at });
  });
  return scored.sort((a, b) => a.score - b.score || a.at - b.at).map((s) => s.it);
}
