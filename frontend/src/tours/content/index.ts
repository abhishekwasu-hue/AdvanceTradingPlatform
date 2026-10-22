import type { TourId } from "../pages";
import type { PageTour } from "../types";
import { BROKER_TOUR } from "./broker";
import { CHARTS_TOUR } from "./charts";
import { COPILOT_TOUR } from "./copilot";
import { DASHBOARD_TOUR } from "./dashboard";
import { OPTION_CHAIN_TOUR } from "./optionChain";
import { PORTFOLIO_TOUR } from "./portfolio";
import { POSITIONS_TOUR } from "./positions";
import { RISK_TOUR } from "./risk";
import { SETTINGS_TOUR } from "./settings";
import { SIGNALS_TOUR } from "./signals";

export { WELCOME, WELCOME_CTA } from "./welcome";

/** Every page tour, by id (one English file per page). Loaded with the tour engine, never in the first page load. */
export const PAGE_TOURS: Record<TourId, PageTour> = {
  dashboard: DASHBOARD_TOUR,
  broker: BROKER_TOUR,
  charts: CHARTS_TOUR,
  "option-chain": OPTION_CHAIN_TOUR,
  signals: SIGNALS_TOUR,
  positions: POSITIONS_TOUR,
  portfolio: PORTFOLIO_TOUR,
  risk: RISK_TOUR,
  settings: SETTINGS_TOUR,
  copilot: COPILOT_TOUR,
};
