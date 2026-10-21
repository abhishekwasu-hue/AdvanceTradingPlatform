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
};

/**
 * Data pages whose v2 layout handles the broker states itself, so the shell does not put the broker gate in front of
 * them (paths as the router matches them). The v2 dashboard shows the gate's own "Connect your broker" / "Try again"
 * card when nothing can be shown, and the rest of the page - with "Log in to <Broker>" in Needs attention and the
 * strip from market memory - when a broker is stored but not logged in today (UI v2 §5, §13 UI-3).
 */
export const OWN_BROKER_STATES: readonly string[] = ["/"];
