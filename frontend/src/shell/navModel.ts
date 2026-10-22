import {
  BarChart3, Bell, BookOpen, Bot, Box, Building2, ChartCandlestick, ClipboardList, Filter, GraduationCap, History, Layers,
  LayoutDashboard, Link2, ListChecks, ListTodo, Newspaper, Pencil, PieChart, Radar, ScrollText, Settings, Shield, ShieldEllipsis,
  ScanSearch, Sigma, Sparkles, Store, UserCircle2, Users, Zap, ChartSpline, Plug, type LucideIcon,
} from "lucide-react";
import { needsAttention, type BrokerDot } from "../broker/brokerState";
import { BROKER_PATH } from "../broker/paths";
import type { Page } from "../components/Sidebar";
import { PAGE_FLAGS } from "./pageFlags";

/**
 * UI2-1 (UI v2 proposal §2 navigation map, §4 bottom bar): the one navigation model the v2 shell reads - the sidebar,
 * the phone bottom bar, the More sheet and the command bar all list pages from here, so they can never disagree.
 *
 * Visibility (§2): "all" items show for every signed-in role; an item with `flag` is hidden while that feature flag
 * is off (or not known yet - fail closed); an item with `role` shows for that role only (Admin console: SUPER_ADMIN).
 * Pages whose feature is off are hidden, never shown disabled (§9 rule 9).
 */

/** A v2 shell location: every routed page, plus the v2-only pages: Charts (`/charts`, `/charts/:symbol`) and
 * Settings → Setup (`/settings/setup`, UI2-7). */
export type ShellKey = Page | "charts" | "setup";

export type SectionId = "markets" | "strategies" | "trading" | "insights" | "account" | "more";

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

/** The five sections plus More, in order (§2 "The v2 sections"; More holds the rarely used tools, UI-10). */
export const NAV_SECTIONS_V2: readonly NavSectionV2[] = [
  {
    id: "markets", title: "Markets", items: [
      item("dashboard", "Dashboard", "Today", "/", LayoutDashboard, { keywords: ["home", "overview", "today"] }),
      item("charts", "Charts", "Charts", "/charts", ChartCandlestick, { keywords: ["chart", "candles", "price"] }),
      item("option-chain", "Option chain", "Option chain", "/option-chain", Link2, { keywords: ["options", "oi", "pcr", "max pain"] }),
      item("screener", "Find setups", "Find setups", "/screener", Filter, { flag: PAGE_FLAGS.screener, keywords: ["screener", "screens", "scan"] }),
    ],
  },
  {
    id: "strategies", title: "Strategies", items: [
      item("strategies", "Library", "Strategy library", "/strategies", Layers, { keywords: ["strategy library", "strategies"] }),
      item("strategy-builder", "Build a strategy", "Build a strategy", "/strategy-builder", Pencil, { keywords: ["strategy builder", "create"] }),
      item("backtest", "Test a strategy", "Test a strategy", "/backtest", History, { keywords: ["backtest", "backtesting"] }),
      item("copilot", "AI Copilot", "AI Copilot", "/copilot", Sparkles, { flag: "ai_copilot", keywords: ["ai", "assistant", "watchtower"] }),
      item("deployments", "Autopilot", "Autopilot", "/deployments", Bot, { keywords: ["deployments", "deploy", "running"] }),
    ],
  },
  {
    id: "trading", title: "Trading", items: [
      item("signals", "Signals", "Signals", "/signals", Zap, { keywords: ["approvals", "alerts", "ideas"] }),
      item("positions", "My positions", "My positions", "/positions", ListChecks, { keywords: ["positions", "holdings", "open trades"] }),
      item("orders", "Orders", "Orders and fills", "/orders", ClipboardList, { keywords: ["fills", "trade history", "blotter"] }),
      item("portfolio", "Portfolio", "My portfolio", "/portfolio", PieChart, { keywords: ["exposure", "money"] }),
      item("risk-management", "Risk", "Risk limits", "/risk-management", Shield, { keywords: ["risk management", "limits", "daily loss"] }),
    ],
  },
  {
    id: "insights", title: "Insights", items: [
      item("analytics", "Performance", "How am I doing?", "/analytics", BarChart3, { keywords: ["analytics", "p&l", "win rate"] }),
      item("journal", "Journal", "Journal", "/journal", BookOpen, { flag: "discipline_engine", keywords: ["journal and discipline", "discipline"] }),
      item("news-events", "News & events", "News & events", "/news-events", Newspaper, { keywords: ["news", "events", "calendar"] }),
      item("fundamentals", "Company research", "Company research", "/fundamentals", Building2, { keywords: ["fundamental analysis", "fundamentals"] }),
    ],
  },
  {
    id: "account", title: "Account", items: [
      item("settings", "Settings", "Settings", "/settings", Settings, { keywords: ["preferences", "appearance", "theme", "integrations", "webhook"] }),
      item("team", "Team", "Team", "/team", Users, { keywords: ["members", "invite"] }),
      item("notifications", "Notifications", "Notifications", "/notifications", Bell, { keywords: ["alerts", "inbox"] }),
    ],
  },
  {
    id: "more", title: "More", items: [
      item("quant", "Factor Lab", "Factor Lab", "/quant", Sigma, { keywords: ["quant", "factors"] }),
      item("instruments", "Contract specs", "Contract specs", "/instruments", Box, { keywords: ["instruments", "lot size", "expiry"] }),
      item("marketplace", "Marketplace", "Strategy marketplace", "/marketplace", Store, { flag: "marketplace", keywords: ["marketplace"] }),
      item("system-logs", "Activity log", "Your account activity", "/system-logs", ScrollText, { keywords: ["system logs", "audit"] }),
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
 * Pages that keep working but have no sidebar entry in v2: they are being merged into another page's tab (§3 M1, M2,
 * M3, M4; the tabs arrive in UI2-8). Until then they live at their new addresses and are found through search (the
 * command bar and the More sheet), so nothing a trader uses today becomes unreachable.
 */
export const NAV_EXTRAS_V2: readonly (NavItemV2 & { section: SectionId })[] = [
  { ...item("scanner", "Custom scan", "Custom scan", "/screener/custom", Radar, { keywords: ["market scanner", "scanner", "scan"] }), section: "markets" },
  { ...item("options-builder", "Plan an options trade", "Plan an options trade", "/option-chain/plan", ChartSpline,
    { flag: "options_builder", keywords: ["options builder", "payoff", "strategy"] }), section: "markets" },
  { ...item("chart-analyze", "Analyze a chart", "Analyze a chart", "/chart-analyze", ScanSearch,
    { flag: "chart_analyze", keywords: ["analyze chart", "analyse chart", "chart reading", "screenshot", "tradingview"] }), section: "markets" },
  { ...item("coach", "Coach and guide", "Coach and guide", "/coach", GraduationCap, { keywords: ["trade coach", "guide", "learn"] }), section: "insights" },
  { ...item("account", "Account", "Account", "/settings/account", UserCircle2, { keywords: ["profile", "password", "two-factor", "sessions", "email"] }), section: "account" },
  // UI2-7: Settings → Setup, the setup wizard and the go-live checklist (§11); Settings v2 groups arrive with UI2-6.
  { ...item("setup", "Setup", "Setup", "/settings/setup", ListTodo, { keywords: ["set up your account", "setup wizard", "get started", "go-live checklist", "readiness"] }), section: "account" },
];

/** The phone bottom bar (§4): Home, Charts, Signals, Positions - and More, which opens the sheet. */
export const BOTTOM_BAR_V2: readonly { key: ShellKey; label: string }[] = [
  { key: "dashboard", label: "Home" },
  { key: "charts", label: "Charts" },
  { key: "signals", label: "Signals" },
  { key: "positions", label: "Positions" },
];

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
  const inBar = new Set<ShellKey>(BOTTOM_BAR_V2.map((b) => b.key));
  return visiblePinned(ctx).filter((i) => !inBar.has(i.key));
}

/** The More sheet (§4): every visible page that is not in the bottom bar, grouped by the same sections. */
export function moreSheetSections(ctx: NavContext): NavSectionV2[] {
  const inBar = new Set<ShellKey>(BOTTOM_BAR_V2.map((b) => b.key));
  return visibleSections(ctx)
    .map((s) => ({ ...s, items: s.items.filter((i) => !inBar.has(i.key)) }))
    .filter((s) => s.items.length > 0);
}

const SECTION_TITLES: Record<SectionId, string> = Object.fromEntries(NAV_SECTIONS_V2.map((s) => [s.id, s.title])) as Record<SectionId, string>;
export const sectionTitle = (id: SectionId): string => SECTION_TITLES[id];

const norm = (s: string) => s.toLowerCase().normalize("NFKD").replace(/[^\p{L}\p{N}&?]+/gu, " ").trim();

/**
 * Pages matching what the trader typed, best first: the label starting with the query, then a word of the label or
 * title starting with it, then the label or title containing it, then a keyword (today's name) matching it. Every
 * word typed must match somewhere. Hidden pages (flag off, other role) are never returned.
 */
export function searchPages(query: string, ctx: NavContext, items = allNavItems()): (NavItemV2 & { section: SectionId })[] {
  const q = norm(query);
  const visible = items.filter((i) => isItemVisible(i, ctx));
  if (!q) return visible;
  const words = q.split(" ");
  const scored: { it: NavItemV2 & { section: SectionId }; score: number; at: number }[] = [];
  visible.forEach((it, at) => {
    const label = norm(it.label);
    const title = norm(it.title);
    const keys = (it.keywords ?? []).map(norm);
    const haystack = [label, title, ...keys, norm(sectionTitle(it.section))].join(" | ");
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
