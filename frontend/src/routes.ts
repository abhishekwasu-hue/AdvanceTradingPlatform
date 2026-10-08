import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { Page } from "./components/Sidebar";

/** P1.1: every page has its own URL (deep links, reload keeps the page, back/forward work) and its own lazily loaded
 * chunk, so the first load carries only the shell. `/` is the dashboard; every other page is `/<page id>`. */
export interface PageProps { onNavigate?: (page: Page) => void }

const loaders: Record<Page, () => Promise<{ default: ComponentType<PageProps> }>> = {
  dashboard: () => import("./pages/DashboardPage"),
  strategies: () => import("./pages/StrategiesPage"),
  "strategy-builder": () => import("./pages/StrategyBuilderPage"),
  deployments: () => import("./pages/DeploymentsPage"),
  fundamentals: () => import("./pages/FundamentalsPage"),
  signals: () => import("./pages/SignalsPage"),
  scanner: () => import("./pages/ScannerPage"),
  "news-events": () => import("./pages/NewsEventsPage"),
  quant: () => import("./pages/QuantPage"),
  backtest: () => import("./pages/BacktestPage"),
  marketplace: () => import("./pages/MarketplacePage"),
  "ai-copilot": () => import("./pages/AiCopilotPage"),
  coach: () => import("./pages/CoachGuidePage"),
  "option-chain": () => import("./pages/OptionChainPage"),
  instruments: () => import("./pages/InstrumentsPage"),
  positions: () => import("./pages/PositionsPage"),
  portfolio: () => import("./pages/PortfolioPage"),
  orders: () => import("./pages/OrdersPage"),
  analytics: () => import("./pages/AnalyticsPage"),
  "risk-management": () => import("./pages/RiskManagementPage"),
  settings: () => import("./pages/SettingsPage"),
  team: () => import("./pages/TeamPage"),
  admin: () => import("./pages/AdminPage"),
  "system-logs": () => import("./pages/SystemLogsPage"),
  notifications: () => import("./pages/NotificationsPage"),
  account: () => import("./pages/AccountPage"),
};

export const PAGES = Object.keys(loaders) as Page[];

export const PAGE_COMPONENTS = Object.fromEntries(
  PAGES.map((p) => [p, lazy(loaders[p])]),
) as Record<Page, LazyExoticComponent<ComponentType<PageProps>>>;

/** Starts downloading a page's chunk before the click lands (sidebar hover / focus). Failures are ignored here - the
 * real navigation reports them through the error boundary. */
export function preloadPage(page: Page): void {
  loaders[page]().catch(() => undefined);
}

export function pathFor(page: Page): string {
  return page === "dashboard" ? "/" : `/${page}`;
}

/** The page a URL shows - the same rule as the router (case-sensitive, one segment, `/ai-copilot/<tab>` the only
 * two-segment path): `/` -> dashboard, `/ai-copilot/today` -> ai-copilot, anything else (`/Settings`,
 * `/settings/foo`) -> null, which renders the not-found card. */
export function pageFromPath(pathname: string): Page | null {
  const parts = pathname.replace(/^\/+|\/+$/g, "").split("/").filter(Boolean);
  if (parts.length === 0) return "dashboard";
  const first = parts[0];
  if (!(PAGES as string[]).includes(first)) return null;
  if (parts.length > (first === "ai-copilot" ? 2 : 1)) return null;
  return first as Page;
}
