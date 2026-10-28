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
    { id: "mood", kind: "mood", title: "Market mood", empty: null, link: null, rows: [
      { type: "cues", mood: "POSITIVE", markets: [
        { key: "SP500_FUT", name: "S&P 500 futures", last: 5872.25, change_pct: 0.62, stale: false },
        { key: "NIKKEI", name: "Nikkei 225 (Japan)", last: 38920.5, change_pct: 0.8, stale: false },
        { key: "HANGSENG", name: "Hang Seng (Hong Kong)", last: 20410.1, change_pct: -0.35, stale: false },
        { key: "BRENT", name: "Brent crude", last: 74.1, change_pct: -0.4, stale: true },
      ] },
      { type: "vix", value: 13.4, change_pct: -2.1, level: "NORMAL", percentile: 31, as_of: null },
      { type: "flows", fii_crore: -1250, dii_crore: 980, as_of: "2026-10-21" },
    ], lines: [
      "Global cues: positive (S&P 500 futures +0.62%, Nikkei 225 (Japan) +0.80%).",
      "India VIX 13.4: normal; higher than 31% of the past year's closes.",
    ] },
    { id: "events", kind: "events", title: "Events today", empty: null, link: { label: "News & events", to: "/news-events" }, rows: [
      { type: "event", date: "2026-10-22", label: "Expiry", scope: "NIFTY", window: null, detail: "Weekly expiry", pauses_entries: false },
    ], lines: [
      "Expiry (NIFTY) - Weekly expiry.",
    ] },
    { id: "structure", kind: "structure", title: "NIFTY 50 and BANK NIFTY", empty: null, link: { label: "Open the chart", to: "/charts/NIFTY%2050" }, rows: [
      { type: "index", symbol: "NIFTY 50", label: "NIFTY 50", trend: "UP", event: { kind: "BOS", direction: "UP" }, location: "NEARER_BUYER",
        buyer_area: { low: 24850, high: 24900 }, seller_area: { low: 25300, high: 25350 }, last_read: null, daily: "UP", weekly: "SIDEWAYS" },
      { type: "index", symbol: "NIFTY BANK", label: "BANK NIFTY", trend: "DOWN", event: { kind: "CHOCH", direction: "DOWN" }, location: "IN_SELLER",
        buyer_area: { low: 53900, high: 54100 }, seller_area: { low: 54800, high: 55000 }, last_read: "2026-10-21" },
    ], lines: [
      "NIFTY 50: higher highs and higher lows; the last swing high was taken out, so the up-structure continued; price is closer to the buyer area below than to the seller area above.",
      "BANK NIFTY: lower highs and lower lows; price is inside a seller area.",
    ] },
    { id: "setups", kind: "setups", title: "Setups from your lists", empty: null, link: { label: "Open Signals", to: "/signals" }, rows: [
      { type: "setup", symbol: "HDFCBANK", side: "LONG", source: "strategy", detail: "a buyer-side setup from your strategy \"Pullback to demand\" - the pullback reached a buyer area." },
      { type: "setup", symbol: "INFY", side: null, source: "scan", detail: "matched your scan \"Range edges\"." },
    ], lines: [
      "HDFCBANK: a buyer-side setup from your strategy \"Pullback to demand\" - the pullback reached a buyer area.",
      "INFY: matched your scan \"Range edges\".",
    ] },
    { id: "positions", kind: "positions", title: "Your open positions", empty: null, link: { label: "My positions", to: "/positions" }, rows: [
      { type: "position", symbol: "TCS", side: "LONG", pnl: 1240, near: false },
      { type: "position", symbol: "SBIN", side: "SHORT", pnl: -380, near: true },
    ], lines: [
      "TCS (long): open P&L +₹1,240.",
      "SBIN (short): open P&L -₹380 - near the invalidation you set for it.",
    ] },
    { id: "budget", kind: "budget", title: "Your risk for the day", empty: null, link: { label: "Risk limits", to: "/risk-management" }, rows: [
      { type: "budget", left: 5000, limit: 5000, used: 0, losses_in_row: 0, pause_after: 3 },
    ], lines: [
      "Loss budget left today: ₹5,000 of ₹5,000 (your own daily loss limit).",
    ] },
  ],
};

export const CLOSING: RhythmBrief = {
  kind: "closing", title: "Closing wrap", subtitle: "Thu 22 Oct · after the close", as_of: "2026-10-22T10:40:00Z", has_content: true, note: NOTE,
  sections: [
    { id: "market", kind: "market", title: "How the market moved", empty: null, link: { label: "Open the chart", to: "/charts/NIFTY%2050" }, rows: [
      { type: "index_move", label: "NIFTY 50", close: 25012.4, change_pct: 0.48, as_of: null, sessions: 1 },
      { type: "index_move", label: "BANK NIFTY", close: 54210.75, change_pct: -0.21, as_of: null, sessions: 1 },
      { type: "breadth", advances: 1412, declines: 1087, as_of: null },
      { type: "sectors", strongest: { label: "IT", change_pct: 1.9 }, weakest: { label: "Metal", change_pct: -1.1 } },
    ], lines: [
      "NIFTY 50 +0.48%, BANK NIFTY -0.21%.", "Breadth: 1,412 shares rose and 1,087 fell.", "Strongest sector: IT (+1.90%); weakest: Metal (-1.10%).",
    ] },
    { id: "day", kind: "day", title: "Your day", empty: null, link: { label: "Open the Journal", to: "/journal" }, rows: [
      { type: "day", booked: 860, open_pnl: null, inside_limits: true, discipline: 92 },
    ], lines: [
      "Booked P&L today: +₹860.", "You stayed inside your own daily limits today. That is the habit that counts.",
      "Discipline score for today: 92 out of 100 (following your own plan, not the number of trades).",
    ] },
    { id: "next", kind: "next", title: "What to watch next session", empty: null, link: { label: "News & events", to: "/news-events" }, rows: [
      { type: "next_session", date: "2026-10-23" },
      { type: "event", date: "2026-10-23", label: "RBI policy", scope: null, window: "10:00-10:30", detail: null, pauses_entries: true },
    ], lines: [
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

/** The same brief as an older server sends it (lines only, no kind / rows): the card falls back to the lines. */
export function linesOnly(brief: RhythmBrief): RhythmBrief {
  return { ...brief, sections: brief.sections.map(({ kind: _k, rows: _r, ...s }) => s) };
}

export const TODAY_PRE: RhythmToday = { phase: "pre_market", kinds: ["pre_market", "mid_session", "closing", "weekly"], brief: PRE_MARKET };
export const TODAY_CLOSING: RhythmToday = { phase: "closing", kinds: ["pre_market", "mid_session", "closing", "weekly"], brief: CLOSING };
