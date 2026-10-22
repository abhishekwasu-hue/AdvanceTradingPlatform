import type { PageTour } from "../types";

/** MO2 part C: the My positions page tips. */
export const POSITIONS_TOUR: PageTour = {
  id: "positions",
  steps: [
    { id: "summary", target: "positions-summary", title: "Your positions in numbers",
      body: "How many positions are open, their profit or loss at live prices, and what was booked today." },
    { id: "list", target: "positions-list", title: "Each position",
      body: "Every open position with its live price. Open its chart from here; a paper position can be closed at the current price." },
    { id: "tools", target: "positions-tools", title: "Your paper account",
      body: "The pretend money your paper trades use, and tools to practise. Nothing here touches real money." },
  ],
};
