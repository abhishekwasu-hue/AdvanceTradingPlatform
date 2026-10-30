import type { PageTour } from "../types";

/** MO2-2: the AI Copilot page tips (the chat-first page at /copilot; its older tool pages have no tips). */
export const COPILOT_TOUR: PageTour = {
  id: "copilot",
  steps: [
    { id: "ask", target: "copilot-ask", only: "desktop", title: "Ask in your own words",
      body: "Type a question about the market or your trades and press Ask. Each answer says how it was worked out." },
    { id: "ask-phone", target: "copilot-ask", only: "phone", title: "Ask in your own words",
      body: "The question box stays just above the bottom bar. Type a question about the market or your trades and tap the arrow." },
    { id: "market", target: "copilot-market", title: "Today's market in numbers",
      body: "The three main indices with their change, the volatility level and the FII net. A number not known yet shows a dash." },
    { id: "jobs", target: "copilot-jobs", title: "Quick questions",
      body: "Today's brief, a chart reading, a check of your trade idea or your open positions: one tap asks it." },
    { id: "more", target: "copilot-more", title: "More Copilot tools",
      body: "The earlier Copilot pages are one tap away here. The Copilot explains; it never places, changes or cancels an order." },
  ],
};
