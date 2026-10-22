import type { RhythmBrief, RhythmToday } from "./types";

/**
 * MO4: digests shaped exactly as the server sends them (backend tests/test_mo4_daily_rhythm.py builds the same kinds of
 * lines from its fixtures), for the vitest and the e2e screenshots. Symbols are placeholders for a user's own list.
 */

const NOTE = "Information from the market and your own records. It describes what the data shows; it does not tell you what to trade.";

export const PRE_MARKET: RhythmBrief = {
  kind: "pre_market", title: "Before the open", subtitle: "Thu 22 Oct · the market opens at 09:15", as_of: "2026-10-22T03:15:00Z",
  has_content: true, note: NOTE,
  sections: [
    { id: "mood", title: "Market mood", empty: null, link: null, lines: [
      "Global cues: positive (S&P 500 futures +0.62%, Nikkei 225 (Japan) +0.80%).",
      "India VIX 13.4: normal; higher than 31% of the past year's closes.",
    ] },
    { id: "events", title: "Events today", empty: null, link: { label: "News & events", to: "/news-events" }, lines: [
      "Expiry (NIFTY) - Weekly expiry.",
    ] },
    { id: "structure", title: "NIFTY 50 and BANK NIFTY", empty: null, link: { label: "Open the chart", to: "/charts/NIFTY%2050" }, lines: [
      "NIFTY 50: higher highs and higher lows; the last swing high was taken out, so the up-structure continued; price is closer to the buyer area below than to the seller area above.",
      "BANK NIFTY: lower highs and lower lows; price is inside a seller area.",
    ] },
    { id: "setups", title: "Setups from your lists", empty: null, link: { label: "Open Signals", to: "/signals" }, lines: [
      "HDFCBANK: a buyer-side setup from your strategy \"Pullback to demand\" - the pullback reached a buyer area.",
      "INFY: matched your scan \"Range edges\".",
    ] },
    { id: "positions", title: "Your open positions", empty: null, link: { label: "My positions", to: "/positions" }, lines: [
      "TCS (long): open P&L +₹1,240.",
      "SBIN (short): open P&L -₹380 - near the invalidation you set for it.",
    ] },
    { id: "budget", title: "Your risk for the day", empty: null, link: { label: "Risk limits", to: "/risk-management" }, lines: [
      "Loss budget left today: ₹5,000 of ₹5,000 (your own daily loss limit).",
    ] },
  ],
};

export const CLOSING: RhythmBrief = {
  kind: "closing", title: "Closing wrap", subtitle: "Thu 22 Oct · after the close", as_of: "2026-10-22T10:40:00Z", has_content: true, note: NOTE,
  sections: [
    { id: "market", title: "How the market moved", empty: null, link: { label: "Open the chart", to: "/charts/NIFTY%2050" }, lines: [
      "NIFTY 50 +0.48%, BANK NIFTY -0.21%.", "Breadth: 1,412 shares rose and 1,087 fell.", "Strongest sector: IT (+1.90%); weakest: Metal (-1.10%).",
    ] },
    { id: "day", title: "Your day", empty: null, link: { label: "Open the Journal", to: "/journal" }, lines: [
      "Booked P&L today: +₹860.", "You stayed inside your own daily limits today. That is the habit that counts.",
      "Discipline score for today: 92 out of 100 (following your own plan, not the number of trades).",
    ] },
    { id: "next", title: "What to watch next session", empty: null, link: { label: "News & events", to: "/news-events" }, lines: [
      "Next session: Fri 23 Oct.", "Fri 23 Oct: RBI policy 10:00-10:30. New entries pause in its window; exits always work.",
    ] },
  ],
};

export const EMPTY_WEEK: RhythmBrief = {
  kind: "weekly", title: "Your week", subtitle: "Week of Mon 19 Oct", as_of: "2026-10-24T04:30:00Z", has_content: false, note: NOTE,
  sections: [
    { id: "market", title: "The week in the market", empty: "The week's closing data is not in yet.", link: null, lines: [] },
    { id: "you", title: "Your week", empty: "No paper trades this week. A quiet week is fine when nothing met your plan.", link: null, lines: [] },
  ],
};

export const TODAY_PRE: RhythmToday = { phase: "pre_market", kinds: ["pre_market", "mid_session", "closing", "weekly"], brief: PRE_MARKET };
export const TODAY_CLOSING: RhythmToday = { phase: "closing", kinds: ["pre_market", "mid_session", "closing", "weekly"], brief: CLOSING };
