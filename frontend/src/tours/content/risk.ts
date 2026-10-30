import type { PageTour } from "../types";

/** MO2 part C: the Risk page tips. M64: the limits are set on Settings › Risk limits; this page keeps today's numbers and
 * the market events, so the steps point at what is on it. */
export const RISK_TOUR: PageTour = {
  id: "risk",
  steps: [
    { id: "today", target: "risk-today", title: "Today's limits",
      body: "The most you allow yourself to lose today and per trade. When a limit is reached, new entries pause." },
    { id: "limits", target: "risk-limits-link", title: "Set your own limits",
      body: "Your limits live on one page: Settings, Risk limits. Each check is a switch, off until you turn it on." },
    { id: "events", target: "risk-events", title: "Market event days",
      body: "Days listed here pause or cut new entries once you switch Market event days on. Exits are never blocked." },
  ],
};
