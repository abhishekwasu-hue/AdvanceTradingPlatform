import type { PageTour } from "../types";

/** MO2 part C: the Signals page tips. */
export const SIGNALS_TOUR: PageTour = {
  id: "signals",
  steps: [
    { id: "tabs", target: "signals-tabs", title: "Today, waiting and history",
      body: "Waiting for you holds the signals you can approve or skip. Today shows everything from today; History keeps the older ones." },
    { id: "filters", target: "signals-filters", title: "Narrow the list",
      body: "Show one source (Autopilot, scans or Copilot) or one symbol. Nothing is filtered until you choose." },
    { id: "list", target: "signals-list", title: "What a signal means",
      body: "Each signal names the setup and the strategy that found it. Open one to see why, then approve or skip it. Nothing is placed until you approve." },
  ],
};
