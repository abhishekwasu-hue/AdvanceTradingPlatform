import type { PageTour } from "../types";

/** MO2 part C: the Risk limits page tips. */
export const RISK_TOUR: PageTour = {
  id: "risk",
  steps: [
    { id: "today", target: "risk-today", title: "Today's limits",
      body: "The most you allow yourself to lose today and per trade. When a limit is reached, new entries pause." },
    { id: "limits", target: "risk-limits", title: "Set your own limits",
      body: "Every paper and live entry is checked against these numbers before it is placed. A limit can never go above the platform's ceiling." },
    { id: "save", target: "risk-save", title: "Save your changes",
      body: "Changes apply once saved. Exits are never blocked by these limits." },
  ],
};
