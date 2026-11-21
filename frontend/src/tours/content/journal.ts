import type { PageTour } from "../types";

/** P2-8 J6: the Journal page tips. The page opens on Today; the other tabs are named in the steps without a target (an
 * automatic tour plays only when every target is on the page). */
export const JOURNAL_TOUR: PageTour = {
  id: "journal",
  steps: [
    { id: "tabs", target: "journal-tabs", title: "Your journal, in tabs",
      body: "Today, your trades, short reviews, My proof and your own plans. Every trade writes its own entry." },
    { id: "today", target: "journal-today", title: "Before the open",
      body: "Your own if-then plans and goals, to re-read before the market opens. Add them on the Plans tab." },
    { id: "checkin", title: "The 20-second check-in",
      body: "After a trade opens or closes, tap how you felt and whether you followed your plan. Every field is optional." },
    { id: "proof", title: "My proof",
      body: "Facts of good process from your own trades, with dates. Pin the ones that matter to you." },
  ],
};
