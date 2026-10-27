import type { PageTour } from "../types";

/** Trading Terminal T1 (owner decision T-D1: a new user starts on the simple "Option seller" layout, with a page tour). */
export const TERMINAL_TOUR: PageTour = {
  id: "terminal",
  steps: [
    { id: "layouts", target: "terminal-layouts", only: "desktop", title: "Your layout",
      body: "Start from a ready layout. Drag a panel by its title to move it, resize it from its corner, then save it as your own." },
    { id: "panels", target: "terminal-panels", title: "Chart, chain and order together",
      body: "Tap a price on the option chain, check the order on the order panel, then place it. On a phone each panel is a tab." },
    { id: "status", target: "terminal-status", title: "The status bar",
      body: "Today's profit or loss, your funds and the margin in use. The eye button hides the amounts on this device." },
    { id: "practice", title: "Practice first",
      body: "In practice mode no real order is sent and no money moves. Exits always work, even when new orders are stopped." },
  ],
};
