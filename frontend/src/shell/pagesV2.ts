import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { PageProps } from "../routes";
import type { ShellKey } from "./navModel";
import { SETTINGS_SECTIONS } from "../settings/paths";

// Settings redesign B: one chunk for every Settings section page (it reads its section from the address).
const SettingsSection = lazy(() => import("../settings/SettingsSectionV2"));

/**
 * UI v2 proposal §12: "the router picks `PageV2 ?? Page` per page". The pages that have a v2 layout, each its own lazy
 * chunk, read only by the v2 shell (so with `ui_v2` off nothing here is downloaded and every page renders as today).
 * A page not listed keeps today's component inside the v2 shell until its own UI2 slice lands.
 */
export const PAGE_COMPONENTS_V2: Partial<Record<ShellKey, LazyExoticComponent<ComponentType<PageProps>>>> = {
  // UI2-2: the dashboard, "Today" (src/dashboard).
  dashboard: lazy(() => import("../dashboard/DashboardV2")),
  // UI2-3: the Signals inbox (§7, src/signals).
  signals: lazy(() => import("../signals/SignalsV2")),
  // UI2-4b2: the Charts page (§6, src/chart) at /charts and /charts/:symbol - a v2-only page (no today's page to replace).
  charts: lazy(() => import("../chart/ChartsPageV2")),
  // OC1: the automatic Option chain page (src/optionChain) at /option-chain - symbol and expiry only, no sample data.
  "option-chain": lazy(() => import("../optionChain/OptionChainPageV2")),
  // UI2-9a: the page sweep's first three (src/trading): My positions, My portfolio, Risk limits.
  positions: lazy(() => import("../trading/PositionsV2")),
  portfolio: lazy(() => import("../trading/PortfolioV2")),
  "risk-management": lazy(() => import("../trading/RiskV2")),
  // UX-10 (My trades wave): My trades › Orders - the fills as a phone-width list, IST times.
  orders: lazy(() => import("../trading/OrdersV2")),
  // UI2-7: Settings → Setup (§11, src/setup) at /settings/setup - the setup wizard, v2 only (P3: the go-live checklist is
  // under Settings → Advanced in both shells).
  setup: lazy(() => import("../setup/SetupPageV2")),
  // Settings redesign part C: Settings → My trading defaults (src/settings) at /settings/trading-defaults - v2 only.
  "trading-defaults": lazy(() => import("../settings/TradingDefaultsV2")),
  // UX2 fix 4: the Trading bot page (src/paperbot) at /deployments - start a bot in three taps, your bots as cards.
  deployments: lazy(() => import("../paperbot/PaperBotV2")),
  // SB-UX SU1-SU6: Strategies › Build as one stepped flow (src/strategyFlow); the full builder of before under Advanced.
  "strategy-builder": lazy(() => import("../strategyFlow/StrategyFlowV2")),
  // Item 5 (owner's handover): the chat-first Copilot (src/copilot/chat) at /copilot; /copilot/<tab> keeps today's tabs.
  copilot: lazy(() => import("../copilot/chat/CopilotPageV2")),
  // Settings redesign B: Settings is a short index with status (src/settings); each row opens its own small page.
  settings: lazy(() => import("../settings/SettingsIndexV2")),
  // MO4 slice 2: the Sector & index view (src/markets) at /markets - v2 only, flag `markets_view`.
  markets: lazy(() => import("../markets/MarketsPageV2")),
  // MO3 addition (a): the admin console in sections (src/admin) at /admin; SUPER_ADMIN only (the server is the gate).
  admin: lazy(() => import("../admin/AdminConsoleV2")),
  // Idea library slice 1 (src/ideas) at /ideas - a v2-only page behind `idea_library`.
  ideas: lazy(() => import("../ideas/IdeaLibraryV2")),
  // Readiness C13 (src/learn) at /learn - a v2-only page behind `learn`.
  learn: lazy(() => import("../learn/LearnPageV2")),
  // Trading Terminal T1 (src/terminal) at /terminal - a v2-only page behind `terminal`.
  terminal: lazy(() => import("../terminal/TerminalPageV2")),
  // P1-8 (2): the Watchlist page (src/watchlist) at /watchlist - a v2-only page behind `watchlists`, the Charts panel.
  watchlist: lazy(() => import("../watchlist/WatchlistPageV2")),
  // M60: Learn › News & events at /learn/news - today's News & events page plus the Copilot's news part (src/learn).
  "news-events": lazy(() => import("../learn/LearnNewsV2")),
  ...Object.fromEntries(SETTINGS_SECTIONS.map((s) => [s.key, SettingsSection])),
};

/**
 * Data pages whose v2 layout handles the broker states itself, so the shell does not put the broker gate in front of
 * them (paths as the router matches them). The v2 dashboard shows the gate's own "Connect your broker" / "Try again"
 * card when nothing can be shown, and the rest of the page - with "Log in to <Broker>" in Needs attention and the
 * strip from market memory - when a broker is stored but not logged in today (UI v2 §5, §13 UI-3).
 *
 * UI2-9a: My positions shows the broker states itself too, so the trader's own open positions keep their exit buttons
 * (ADR-0004) while no broker session is usable: the gate's card says what is missing, and below it only the trader's
 * real open positions at the entry price - no live price, no P&L and nothing made up (src/trading/PositionsV2.tsx).
 */
/*
 * Item 5: the chat-first Copilot (`/copilot` only) answers from ATP's shared data and says itself what is missing ("I
 * don't know the option chain: connect your broker ..."), so the gate does not hide it; `/copilot/<tab>` stays gated.
 */
export const OWN_BROKER_STATES: readonly string[] = ["/", "/positions", "/copilot"];
