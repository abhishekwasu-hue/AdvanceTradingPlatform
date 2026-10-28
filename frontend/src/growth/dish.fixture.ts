import type { PageDish } from "./types";

/** MO4 slice 4: made-up dishes in the shape of backend/tests/test_mo4_dishes.py (no real person, no real figure). Used
 * by the vitest and e2e suites only. */

const NOTE = "Information from your own records and the market's data. It describes what the data shows; it does not tell you what to trade.";
const AT = "2032-03-17T08:30:00+00:00";

export const DEMO_DISHES: Record<PageDish["page"], PageDish> = {
  signals: {
    page: "signals", title: "Your signals at a glance", available: true, as_of: AT, note: NOTE,
    headline: "2 signals wait for your decision.",
    sections: [
      { id: "waiting", kind: "waiting", title: "Waiting for your decision", empty: null, link: null, rows: [
        { type: "waiting", count: 2 },
        { type: "setup", symbol: "DEMOALPHA", side: "LONG", source: "scan", detail: "matched your scan \"Pullback\" - pullback reached a buyer area." },
        { type: "setup", symbol: "DEMOBETA", side: "SHORT", source: "strategy", detail: "a seller-side setup from your strategy \"Range edges\" - price is at the upper edge of the range." },
      ], lines: [
        "2 signals wait for your decision.",
        "DEMOALPHA: matched your scan \"Pullback\" - pullback reached a buyer area.",
        "DEMOBETA: a seller-side setup from your strategy \"Range edges\" - price is at the upper edge of the range.",
      ] },
      { id: "today", kind: "sources", title: "Today so far", empty: null, link: null, rows: [
        { type: "sources", counts: { autopilot: 3, scan: 5 }, words: { autopilot: "your paper bots", scan: "your scans" }, more: false },
      ], lines: ["3 from your paper bots and 5 from your scans."] },
      { id: "record", kind: "record", title: "Your strategies' paper record", empty: null, link: null, rows: [
        { type: "strategy_record", name: "Pullback", closed: 12, won: 7, lost: 5, days: 90 },
        { type: "strategy_record", name: "Range edges", closed: 4, won: 2, lost: 2, days: 90 },
      ], lines: [
        "Pullback: 12 closed paper trades in the last 90 days; 7 ended with a gain and 5 did not.",
        "Range edges: 4 closed paper trades in the last 90 days; 2 ended with a gain and 2 did not.",
      ] },
    ],
  },
  positions: {
    page: "positions", title: "Your positions now", available: true, as_of: AT, note: NOTE,
    headline: "Open P&L across 3 positions: +₹1,240 at the last prices.",
    sections: [
      { id: "risk", kind: "risk", title: "Your risk in plain words", empty: null, link: null, rows: [
        { type: "risk", positions: 3, open_pnl: 1240, prices_missing: false, at_invalidation: -2150, of_loss_limit_pct: 54, no_invalidation: 0 },
      ], lines: [
        "Open P&L across 3 positions: +₹1,240 at the last prices.",
        "If each position reached the invalidation you set for it, the result from entry would be about -₹2,150 in total.",
        "That is 54% of your own daily loss limit.",
      ] },
      { id: "near", kind: "positions", title: "Near their invalidation", empty: null, link: null, rows: [
        { type: "position", symbol: "DEMOALPHA", side: "LONG", pnl: -410, near: true },
      ], lines: ["DEMOALPHA (long): close to the invalidation you set for it."] },
      { id: "premium", kind: "premium", title: "Premium kept on written options", empty: null, link: null, rows: [
        { type: "premium", label: "Iron condor on DEMOIDX", kept_pct: 62, kept: 3750, received: 6000 },
      ], lines: [
        "Iron condor on DEMOIDX: 62% of the premium kept so far (+₹3,750 of ₹6,000).",
      ] },
      { id: "ahead", kind: "events", title: "Coming up for what you hold", empty: null, link: null, rows: [
        { type: "event", date: "2032-03-19", label: "RBI policy", scope: null, window: null, detail: null, pauses_entries: false },
        { type: "expiry", date: "2032-03-18", underlying: "DEMOIDX", count: 4 },
      ], lines: [
        "Thu 18 Mar: your DEMOIDX contracts expire (4 positions).",
        "Fri 19 Mar: RBI policy.",
      ] },
    ],
  },
  journal: {
    page: "journal", title: "Your journal at a glance", available: true, as_of: AT, note: NOTE,
    headline: "Discipline score this week: 78 out of 100.",
    sections: [
      { id: "score", kind: "discipline", title: "Your discipline", empty: null, link: null, rows: [
        { type: "discipline", score: 78, previous: 74, history: [65, 70, 74] },
      ], lines: [
        "Discipline score this week: 78 out of 100.",
        "That is up 4 on last week. The weeks before: 65, 70, 74 (oldest first).",
        "It counts how closely you followed your own plan, not how many trades you took.",
      ] },
      { id: "setups", kind: "extremes", title: "Your setups over the last four weeks", empty: null, link: null, rows: [
        { type: "best", name: "Pullback", net: 4200, closed: 6 },
        { type: "worst", name: "Breakout", net: -1850, closed: 3 },
      ], lines: [
        "Best: Pullback (+₹4,200 over 6 closed trades).",
        "Cost the most: Breakout (-₹1,850 over 3 closed trades).",
      ] },
      { id: "habits", kind: "habits", title: "Patterns this week", empty: null, link: null, rows: [
        { type: "habit", label: "A trade soon after a loss (revenge trade)", trades: 1, net: -600 },
        { type: "suggested_tags", count: 2 },
      ], lines: [
        "A trade soon after a loss (revenge trade): 1 trade, net -₹600.",
        "2 suggested tags wait for you to confirm or dismiss on the Week tab.",
      ] },
    ],
  },
  charts: {
    page: "charts", title: "What this chart shows", available: true, as_of: AT, note: NOTE,
    headline: "Daily swings: higher highs and higher lows.",
    sections: [
      { id: "story", kind: "story", title: "The chart's story", empty: null, link: null, rows: [
        { type: "story", daily: "UP", event: { kind: "BOS", direction: "UP" }, place: "BETWEEN", weekly: "SIDEWAYS", as_of: null },
      ], lines: [
        "Daily swings: higher highs and higher lows; most recently the last swing high was taken out, so the up-structure continued; the last close is between the last swing low and the last swing high.",
        "Weekly swings: no clear swing sequence (sideways).",
      ] },
      { id: "yours", kind: "yours", title: "Yours on DEMOALPHA", empty: null, link: null, rows: [
        { type: "held", count: 1, open_pnl: 320, near: false },
        { type: "setup", symbol: "DEMOALPHA", side: "LONG", source: "scan", detail: "matched your scan \"Pullback\" - pullback reached a buyer area.", today: true },
        { type: "lists", names: ["Swing ideas"] },
      ], lines: [
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
      { id: "today", kind: "matches", title: "Today's matches", empty: null, link: { label: "Open Signals", to: "/signals" }, rows: [
        { type: "scan", name: "Pullback to demand", count: 4, symbols: ["DEMOALPHA", "DEMOBETA", "DEMOGAMMA", "DEMODELTA"] },
        { type: "scan", name: "Range edges", count: 2, symbols: ["DEMOEPSILON", "DEMOZETA"] },
        { type: "on_lists", symbols: ["DEMOALPHA", "DEMOZETA"] },
      ], lines: [
        "\"Pullback to demand\": 4 matches - DEMOALPHA, DEMOBETA, DEMOGAMMA, DEMODELTA.",
        "\"Range edges\": 2 matches - DEMOEPSILON, DEMOZETA.",
        "On your own lists: DEMOALPHA, DEMOZETA.",
      ] },
      { id: "scans", kind: "scans", title: "Your scans that run on their own", empty: null, link: null, rows: [{ type: "active", count: 2 }], lines: [
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
