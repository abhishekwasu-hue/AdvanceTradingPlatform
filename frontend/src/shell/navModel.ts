import {
  BarChart3, Bell, BookOpen, Bot, Box, Building2, ChartCandlestick, ClipboardList, Eye, Filter, GraduationCap, History, Layers,
  LayoutDashboard, LayoutGrid, Lightbulb, Link2, ListChecks, ListTodo, MonitorDot, Newspaper, Pencil, PieChart, Radar, ScrollText, Settings, Shield, ShieldEllipsis,
  ScanSearch, Sigma, SlidersHorizontal, Sparkles, Store, UserCircle2, Users, Zap, ChartSpline, Plug, Palette, LifeBuoy, Wrench, type LucideIcon,
} from "lucide-react";
import { needsAttention, type BrokerDot } from "../broker/brokerState";
import { BROKER_PATH } from "../broker/paths";
import type { Page } from "../components/Sidebar";
import { PAGE_FLAGS } from "./pageFlags";
import { SETTINGS_PATH, type SettingsSectionKey } from "../settings/paths";
import { IDEAS_ADDRESS, IDEA_FLAG } from "../ideas/model";
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
 * Settings → Setup (`/settings/setup`, UI2-7), My trading defaults (`/settings/trading-defaults`, part C) and the Settings section pages (`/settings/ai` ..., Settings redesign B). */
export type ShellKey = Page | "charts" | "setup" | "trading-defaults" | "ideas" | "markets" | "learn" | "terminal" | "watchlist" | SettingsSectionKey;

export type SectionId = "markets" | "strategies" | "trading" | "insights" | "account" | "more" | "admin";

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
}

export interface NavSectionV2 {
  id: SectionId;
  title: string;
  items: NavItemV2[];
}

const item = (key: ShellKey, label: string, title: string, path: string, icon: LucideIcon, extra: Partial<NavItemV2> = {}): NavItemV2 =>
  ({ key, label, title, path, icon, ...extra });

/**
 * UX3-9 (UX audit section 5, "The proposed sidebar"): 13 entries in task order, Broker first (pinned below), in one flat
 * list - the sidebar shows no section headings and nothing folds away. The groups below only give the phone's More
 * sheet its headings and the search results their captions. Two entries appear only with their feature on (Sectors &
 * indices, Find setups, AI Copilot) and the Admin console only for the platform operator, set apart at the bottom.
 *
 * The merged pages (audit section 5 "Merge") are tabs of their entry (src/shell/pageGroups.ts): My trades (Positions ·
 * Orders · Money), Strategies (Library · Ideas · Build · Test) and Performance (Results · Journal · Coach). Every other
 * page keeps working and is found by search (NAV_EXTRAS_V2), so nothing a trader uses today becomes unreachable.
 */
export const NAV_SECTIONS_V2: readonly NavSectionV2[] = [
  {
    id: "markets", title: "Markets", items: [
      item("dashboard", "Today", "Today", "/", LayoutDashboard, { keywords: ["home", "overview", "dashboard"] }),
      item("charts", "Charts", "Charts", "/charts", ChartCandlestick, { keywords: ["chart", "candles", "price"] }),
      // P1-8 (2): the Watchlist page (v2 only, flag `watchlists`) - the same panel as on Charts, on its own page.
      item("watchlist", "Watchlist", "Watchlist", WATCHLIST_ADDRESS, Eye,
        { flag: PAGE_FLAGS.watchlist, keywords: ["watchlists", "watch list", "my lists", "indices", "index stocks", "sectors", "f&o", "prices"] }),
      item("option-chain", "Option chain", "Option chain", "/option-chain", Link2, { keywords: ["options", "oi", "pcr", "max pain"] }),
      // MO4 slice 2: the Sector & index view (v2 only, flag `markets_view`).
      item("markets", "Sectors & indices", "Sectors & indices", "/markets", LayoutGrid,
        { flag: PAGE_FLAGS.markets, keywords: ["sectors", "indices", "heat map", "heatmap", "sector rotation", "breadth", "market"] }),
      item("screener", "Find setups", "Find setups", "/screener", Filter, { flag: PAGE_FLAGS.screener, keywords: ["screener", "screens", "scan"] }),
    ],
  },
  {
    id: "trading", title: "Trading", items: [
      // Trading Terminal T1 (TT-18): first in the Trading group, only while `terminal` is on (13 entries off, 14 on).
      item("terminal", "Terminal", "Terminal", TERMINAL_ADDRESS, MonitorDot,
        { flag: PAGE_FLAGS.terminal, keywords: ["trading terminal", "trade screen", "workspace", "order panel", "scalper", "option seller"] }),
      item("signals", "Signals", "Signals", "/signals", Zap, { keywords: ["approvals", "alerts", "ideas"] }),
      item("deployments", "Paper bot", "Paper bot", "/deployments", Bot, { keywords: ["autopilot", "deployments", "deploy", "running", "bots"] }),
      item("positions", "My trades", "My trades", "/positions", ListChecks, { keywords: ["my positions", "positions", "holdings", "open trades", "closed trades"] }),
    ],
  },
  {
    id: "insights", title: "Insights", items: [
      item("analytics", "Performance", "How am I doing?", "/analytics", BarChart3, { keywords: ["analytics", "p&l", "win rate", "results"] }),
      item("strategies", "Strategies", "Strategies", "/strategies", Layers, { keywords: ["strategy library", "library"] }),
      item("copilot", "AI Copilot", "AI Copilot", "/copilot", Sparkles, { flag: "ai_copilot", keywords: ["ai", "assistant", "watchtower"] }),
      item("news-events", "News & events", "News & events", "/news-events", Newspaper, { keywords: ["news", "events", "calendar"] }),
      // Readiness C13 (flag `learn`): short lessons, the glossary and the course videos (education only).
      item("learn", "Learn", "Learn", LEARN_ADDRESS, GraduationCap,
        { flag: PAGE_FLAGS.learn, keywords: ["lessons", "course", "videos", "glossary", "words", "basics", "beginner", "how to"] }),
    ],
  },
  {
    id: "account", title: "Account", items: [
      item("settings", "Settings", "Settings", "/settings", Settings, { keywords: ["preferences", "appearance", "theme", "integrations", "webhook"] }),
    ],
  },
  {
    // Audit section 5: the admin area is separate, not mixed into the user list (SUPER_ADMIN only; the server is the gate).
    id: "admin", title: "Admin", items: [
      item("admin", "Admin console", "Admin console", "/admin", ShieldEllipsis, { role: "SUPER_ADMIN", keywords: ["admin", "platform"] }),
    ],
  },
];

/**
 * P2b: the entries above the sections - always listed, never folded away: "Broker" first, with its status dot (green
 * connected, amber needs login, red not working). On a phone it is the first tile of the More sheet. `section` is only
 * the caption search results show.
 */
export const NAV_PINNED_V2: readonly (NavItemV2 & { section: SectionId })[] = [
  { ...item("broker", "Broker", "Broker", BROKER_PATH, Plug, { keywords: ["brokers", "connect broker", "renew", "login", "upstox", "zerodha"] }), section: "account" },
];

/** The pinned entries this trader may see. */
export function visiblePinned(ctx: NavContext): NavItemV2[] {
  return NAV_PINNED_V2.filter((i) => isItemVisible(i, ctx));
}

/** The dot an entry carries: the broker's status on "Broker", nothing elsewhere. */
export function navDotFor(key: ShellKey, dot: BrokerDot | null): BrokerDot | null {
  return key === "broker" ? dot : null;
}

/** The phone's More button carries the dot too while a broker needs attention (amber or red). */
export function moreButtonDot(dot: BrokerDot | null): BrokerDot | null {
  return needsAttention(dot) ? dot : null;
}

/**
 * Pages that keep working but have no sidebar entry: the tabs of a merged page (UX3-9, at their new addresses; the old
 * addresses forward there), the sub-pages of Settings, and the rarely used tools the audit takes out of the user
 * navigation (section 5 "Drop from the user navigation"). They are found through search (the command bar, the More
 * sheet and the Settings index), so nothing a trader uses today becomes unreachable.
 */
export const NAV_EXTRAS_V2: readonly (NavItemV2 & { section: SectionId })[] = [
  { ...item("scanner", "Custom scan", "Custom scan", "/screener/custom", Radar, { keywords: ["market scanner", "scanner", "scan"] }), section: "markets" },
  { ...item("options-builder", "Plan an options trade", "Plan an options trade", "/option-chain/plan", ChartSpline,
    { flag: "options_builder", keywords: ["options builder", "payoff", "strategy"] }), section: "markets" },
  { ...item("chart-analyze", "Analyze a chart", "Analyze a chart", "/chart-analyze", ScanSearch,
    { flag: "chart_analyze", keywords: ["analyze chart", "analyse chart", "chart reading", "screenshot", "tradingview"] }), section: "markets" },
  // UX3-9: My trades' tabs (the old /orders and /portfolio forward here).
  { ...item("orders", "Orders", "My trades", "/positions/orders", ClipboardList, { keywords: ["orders and fills", "fills", "trade history", "blotter"] }), section: "trading" },
  { ...item("portfolio", "Money", "My trades", "/positions/money", PieChart, { keywords: ["portfolio", "my portfolio", "exposure", "where my money is"] }), section: "trading" },
  // UX3-9: Strategies' tabs (Ideas keeps its own address; the old /strategy-builder and /backtest forward here).
  // Idea library slice 1 (flag idea_library): method ideas in plain words, each with a scan to try (education only).
  { ...item("ideas", "Ideas", "Strategies", IDEAS_ADDRESS, Lightbulb, { flag: IDEA_FLAG, keywords: ["idea library", "ideas", "learn", "methods", "price action"] }), section: "strategies" },
  { ...item("strategy-builder", "Build a strategy", "Strategies", "/strategies/build", Pencil, { keywords: ["strategy builder", "create", "build"] }), section: "strategies" },
  { ...item("backtest", "Test a strategy", "Strategies", "/strategies/test", History, { keywords: ["backtest", "backtesting", "test"] }), section: "strategies" },
  // UX3-9: Performance's tabs (the old /journal, /coach and /journal/coach forward here).
  { ...item("journal", "Journal", "How am I doing?", "/analytics/journal", BookOpen, { flag: "discipline_engine", keywords: ["journal and discipline", "discipline"] }), section: "insights" },
  { ...item("coach", "Coach", "How am I doing?", "/analytics/coach", GraduationCap, { keywords: ["coach and guide", "trade coach", "guide", "learn"] }), section: "insights" },
  { ...item("fundamentals", "Company research", "Company research", "/fundamentals", Building2, { keywords: ["fundamental analysis", "fundamentals"] }), section: "insights" },
  // Settings' sub-pages (audit section 5: "set-once pages leave the main nav"); the Settings index lists them too.
  { ...item("risk-management", "Risk limits", "Risk limits", "/risk-management", Shield, { keywords: ["risk", "risk management", "limits", "daily loss"] }), section: "account" },
  { ...item("team", "Team", "Team", "/team", Users, { keywords: ["members", "invite"] }), section: "account" },
  { ...item("notifications", "Notifications", "Notifications", "/notifications", Bell, { keywords: ["alerts", "inbox"] }), section: "account" },
  { ...item("account", "Account", "Account", "/settings/account", UserCircle2, { keywords: ["profile", "password", "two-factor", "sessions", "email"] }), section: "account" },
  // UI2-7: Settings → Setup, the setup wizard and the go-live checklist (§11); Settings v2 groups arrive with UI2-6.
  { ...item("setup", "Setup", "Setup", "/settings/setup", ListTodo, { keywords: ["set up your account", "setup wizard", "get started", "go-live checklist", "readiness"] }), section: "account" },
  // Settings redesign part C: the user's own starting values for new bots and chain tickets (a pre-fill, never a rule).
  { ...item("trading-defaults", "My trading defaults", "My trading defaults", "/settings/trading-defaults", SlidersHorizontal,
    { keywords: ["defaults", "default lots", "presets", "order presets", "expiry rule", "strike rule", "trailing stop"] }), section: "account" },
  // Settings redesign B: the Settings section pages (src/settings), found by search too.
  { ...item("settings-ai", "AI", "AI", SETTINGS_PATH.ai, Sparkles, { keywords: ["ai key", "api key", "connect ai", "claude", "openai", "gemini", "ai limits"] }), section: "account" },
  { ...item("settings-alerts", "Alerts", "Alerts", SETTINGS_PATH.alerts, Bell, { keywords: ["telegram", "alert channels", "email alerts", "sms", "push notifications"] }), section: "account" },
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
 * UX3-9: the sidebar entry a page belongs to - a merged page's tab lights up its entry (Orders -> My trades), a Settings
 * sub-page lights up Settings, a page's own sub-page its page (Custom scan -> Find setups). Null: none.
 */
const NAV_HOME: Partial<Record<ShellKey, ShellKey>> = {
  orders: "positions", portfolio: "positions",
  ideas: "strategies", "strategy-builder": "strategies", backtest: "strategies",
  journal: "analytics", coach: "analytics",
  scanner: "screener", "options-builder": "option-chain",
  "risk-management": "settings", team: "settings", account: "settings", setup: "settings", "trading-defaults": "settings",
  "settings-ai": "settings", "settings-alerts": "settings", "settings-appearance": "settings", "settings-help": "settings",
  "settings-advanced": "settings",
};

/** The sidebar / bottom bar entry that shows as current for the page on screen. */
export function navEntryFor(key: ShellKey | null): ShellKey | null {
  if (key === null) return null;
  return NAV_HOME[key] ?? key;
}

/** UX audit section 5: the phone bottom bar - Today, Charts, Signals, My trades - and More, which opens the sheet.
 * UI-28 U3.5 (owner default, 28 Oct; Material / HIG: 3-5 destinations): five with More, so Watchlist is not on the bar -
 * it lives in the Charts panel and in More. (An entry with `flag` shows only while that flag is on.) */
export const BOTTOM_BAR_V2: readonly { key: ShellKey; label: string; flag?: string }[] = [
  { key: "dashboard", label: "Today" },
  { key: "charts", label: "Charts" },
  { key: "signals", label: "Signals" },
  { key: "positions", label: "My trades" },
];

/** The bottom bar's entries this trader may see (no context: the entries without a flag). */
export function visibleBottomBar(ctx?: Pick<NavContext, "on">): { key: ShellKey; label: string }[] {
  return BOTTOM_BAR_V2.filter((b) => !b.flag || !!ctx?.on(b.flag)).map(({ key, label }) => ({ key, label }));
}

/** Who is looking and which flags are on - all the navigation needs to decide what to list. */
export interface NavContext {
  /** True only when the flag is known and on (useFeatures().on). */
  on: (flag: string) => boolean;
  role: string | null;
}

export function isItemVisible(it: Pick<NavItemV2, "flag" | "role">, ctx: NavContext): boolean {
  if (it.flag && !ctx.on(it.flag)) return false;
  if (it.role && ctx.role !== it.role) return false;
  return true;
}

/** The sections with only the items this trader may see; a section left empty is dropped. */
export function visibleSections(ctx: NavContext, sections: readonly NavSectionV2[] = NAV_SECTIONS_V2): NavSectionV2[] {
  return sections
    .map((s) => ({ ...s, items: s.items.filter((i) => isItemVisible(i, ctx)) }))
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

/** The section that holds a location, so the sidebar can open it (none for a pinned entry: it is always shown). */
export function sectionOf(key: ShellKey | null): SectionId | null {
  if (NAV_PINNED_V2.some((p) => p.key === key)) return null;
  return navItemFor(key)?.section ?? null;
}

/** P2b: the More sheet's first tiles, above the sections: the pinned entries (Broker). */
export function moreSheetPinned(ctx: NavContext): NavItemV2[] {
  const inBar = new Set<ShellKey>(visibleBottomBar(ctx).map((b) => b.key));
  return visiblePinned(ctx).filter((i) => !inBar.has(i.key));
}

/** The More sheet (§4): every visible page that is not in the bottom bar, grouped by the same sections. */
export function moreSheetSections(ctx: NavContext): NavSectionV2[] {
  const inBar = new Set<ShellKey>(visibleBottomBar(ctx).map((b) => b.key));
  return visibleSections(ctx)
    .map((s) => ({ ...s, items: s.items.filter((i) => !inBar.has(i.key)) }))
    .filter((s) => s.items.length > 0);
}

/** The caption of each group (search results, the More sheet); "Strategies" and "More" only caption search results. */
const SECTION_TITLES: Record<SectionId, string> = {
  markets: "Markets", strategies: "Strategies", trading: "Trading", insights: "Insights", account: "Account", more: "More", admin: "Admin",
};
export const sectionTitle = (id: SectionId): string => SECTION_TITLES[id];

const norm = (s: string) => s.toLowerCase().normalize("NFKD").replace(/[^\p{L}\p{N}&?]+/gu, " ").trim();

/** What search reads from an entry: a page (allNavItems) or, on the Settings index, a setting inside a section. */
export type Searchable = Pick<NavItemV2, "label" | "title" | "keywords" | "flag" | "role"> & { section?: SectionId };

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
