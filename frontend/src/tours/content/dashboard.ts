import type { PageTour } from "../types";

/** MO2 part C: the Dashboard ("Today") tips. */
export const DASHBOARD_TOUR: PageTour = {
  id: "dashboard",
  steps: [
    { id: "strip", target: "dashboard-strip", title: "The market right now",
      body: "The strip at the top shows the main indices, whether the market is open and your profit or loss for today." },
    { id: "attention", target: "dashboard-attention", title: "Start here",
      body: "Needs attention lists what is waiting for you, such as a signal to review or a broker to log in to. When it says All clear, nothing needs you." },
    { id: "positions", target: "dashboard-positions", title: "Your open positions",
      body: "Each line is a position you hold, with its profit or loss at the live price. View all opens My positions." },
    { id: "signals", target: "dashboard-signals", title: "New signals",
      body: "Signals your Autopilot and scans found today. Nothing is placed until you approve it." },
    { id: "nav-desktop", target: "nav-sidebar", only: "desktop", title: "Every page in one place",
      body: "The menu on the left lists every page. Broker sits at the top, with a dot that shows whether it is connected." },
    { id: "nav-phone", target: "nav-bottom", only: "phone", title: "Move between pages",
      body: "The bar at the bottom opens Charts, Signals and Positions. More holds every other page, Broker first." },
  ],
};
