import type { PageDish } from "./types";

/** MO4 slice 4: made-up dishes in the shape of backend/tests/test_mo4_dishes.py (no real person, no real figure). Used
 * by the vitest and e2e suites only. */

const NOTE = "Information from your own records and the market's data. It describes what the data shows; it does not tell you what to trade.";
const AT = "2032-03-17T08:30:00+00:00";

export const DEMO_DISHES: Record<PageDish["page"], PageDish> = {
  signals: {
    page: "signals", title: "Your signals at a glance", available: true, as_of: AT, note: NOTE,
    headline: "2 signals wait for your decision. Nothing is placed until you approve it.",
    sections: [
      { id: "waiting", title: "Waiting for your decision", empty: null, link: null, lines: [
        "2 signals wait for your decision. Nothing is placed until you approve it.",
        "DEMOALPHA: matched your scan \"Pullback\" - pullback reached a buyer area.",
        "DEMOBETA: a seller-side setup from your strategy \"Range edges\" - price is at the upper edge of the range.",
      ] },
      { id: "today", title: "Today so far", empty: null, link: null, lines: ["3 from your paper bots and 5 from your scans."] },
      { id: "record", title: "Your strategies' paper record", empty: null, link: null, lines: [
        "Pullback: 12 closed paper trades in the last 90 days; 7 ended with a gain and 5 did not.",
        "Range edges: 4 closed paper trades in the last 90 days; 2 ended with a gain and 2 did not.",
      ] },
    ],
  },
  positions: {
    page: "positions", title: "Your positions now", available: true, as_of: AT, note: NOTE,
    headline: "Open P&L across 3 positions: +₹1,240 at the last prices.",
    sections: [
      { id: "risk", title: "Your risk in plain words", empty: null, link: null, lines: [
        "Open P&L across 3 positions: +₹1,240 at the last prices.",
        "If each position reached the invalidation you set for it, the result from entry would be about -₹2,150 in total.",
        "That is 54% of your own daily loss limit.",
      ] },
      { id: "near", title: "Near their invalidation", empty: null, link: null, lines: ["DEMOALPHA (long): close to the invalidation you set for it."] },
      { id: "premium", title: "Premium kept on written options", empty: null, link: null, lines: [
        "Iron condor on DEMOIDX: 62% of the premium kept so far (+₹3,750 of ₹6,000).",
      ] },
      { id: "ahead", title: "Coming up for what you hold", empty: null, link: null, lines: [
        "Thu 18 Mar: your DEMOIDX contracts expire (4 positions).",
        "Fri 19 Mar: RBI policy.",
      ] },
    ],
  },
  journal: {
    page: "journal", title: "Your journal at a glance", available: true, as_of: AT, note: NOTE,
    headline: "Discipline score this week: 78 out of 100.",
    sections: [
      { id: "score", title: "Your discipline", empty: null, link: null, lines: [
        "Discipline score this week: 78 out of 100.",
        "That is up 4 on last week. The weeks before: 65, 70, 74 (oldest first).",
        "It counts how closely you followed your own plan, not how many trades you took.",
      ] },
      { id: "setups", title: "Your setups over the last four weeks", empty: null, link: null, lines: [
        "Best: Pullback (+₹4,200 over 6 closed trades).",
        "Cost the most: Breakout (-₹1,850 over 3 closed trades).",
      ] },
      { id: "habits", title: "Patterns this week", empty: null, link: null, lines: [
        "A trade soon after a loss (revenge trade): 1 trade, net -₹600.",
        "2 suggested tags wait for you to confirm or dismiss on the Week tab.",
      ] },
    ],
  },
  charts: {
    page: "charts", title: "What this chart shows", available: true, as_of: AT, note: NOTE,
    headline: "Daily swings: higher highs and higher lows.",
    sections: [
      { id: "story", title: "The chart's story", empty: null, link: null, lines: [
        "Daily swings: higher highs and higher lows; most recently the last swing high was taken out, so the up-structure continued; the last close is between the last swing low and the last swing high.",
        "Weekly swings: no clear swing sequence (sideways).",
      ] },
      { id: "yours", title: "Yours on DEMOALPHA", empty: null, link: null, lines: [
        "You hold 1 open paper position here, open P&L +₹320.",
        "Your latest setup here, today: matched your scan \"Pullback\" - pullback reached a buyer area.",
        "On your lists: Swing ideas.",
      ] },
    ],
  },
  screener: {
    page: "screener", title: "Today's setups from your scans", available: true, as_of: AT, note: NOTE,
    headline: "6 symbols matched your scans today.",
    sections: [
      { id: "today", title: "Today's matches", empty: null, link: { label: "Open Signals", to: "/signals" }, lines: [
        "\"Pullback to demand\": 4 matches - DEMOALPHA, DEMOBETA, DEMOGAMMA, DEMODELTA.",
        "\"Range edges\": 2 matches - DEMOEPSILON, DEMOZETA.",
        "On your own lists: DEMOALPHA, DEMOZETA.",
      ] },
      { id: "scans", title: "Your scans that run on their own", empty: null, link: null, lines: [
        "2 scans of yours run on their own; new matches show here and on Signals.",
      ] },
    ],
  },
};

/** A dish with nothing to say yet: every section says so in plain words. */
export const EMPTY_SIGNALS: PageDish = {
  page: "signals", title: "Your signals at a glance", available: true, as_of: AT, note: NOTE,
  headline: "Nothing waits for your decision right now.",
  sections: [
    { id: "waiting", title: "Waiting for your decision", lines: [], link: null,
      empty: "Nothing waits for your decision right now. New signals from your paper bots and scans arrive here." },
    { id: "today", title: "Today so far", lines: [], link: null,
      empty: "No new signals today yet. They appear here when your paper bots or scans find a setup from your own rules." },
  ],
};
