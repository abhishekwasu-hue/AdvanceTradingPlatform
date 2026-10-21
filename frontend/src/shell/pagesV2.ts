import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { PageProps } from "../routes";
import type { ShellKey } from "./navModel";

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
  // UI2-7: Settings → Setup (§11, src/setup) at /settings/setup - the setup wizard and the go-live checklist, v2 only.
  setup: lazy(() => import("../setup/SetupPageV2")),
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
export const OWN_BROKER_STATES: readonly string[] = ["/", "/positions"];
