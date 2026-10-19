/**
 * N1 F3: the portfolio risk card strings (namespace `portfolioRisk`, locale `en`). English only
 * (scripts/check_english_only.py). Numbers of the person's own PAPER book and their own limits - never advice: no
 * "should", "buy", "sell", "reduce" or "recommend" (portfolioRisk.test.tsx checks every string).
 */
export const PORTFOLIO_RISK_EN = {
  title: "Portfolio risk (your PAPER book)",
  loading: "Reading your book…",
  failed: "Portfolio risk could not be read: {{error}}",
  empty: "No open PAPER positions.",
  asOf: "As of {{day}} · capital {{capital}} ({{source}})",
  tiles: {
    exposure: "Exposure",
    net: "Net",
    openRisk: "Open risk at the stops",
    beta: "NIFTY 50 beta-weighted net",
  },
  ofCapital: "{{pct}} of capital",
  buckets: {
    title: "By bucket",
    bucket: "Bucket",
    positions: "Positions",
    exposure: "Exposure",
    risk: "Open risk",
    index: "All indices (one bucket)",
    unclassified: "{{name}} (no sector on record)",
  },
  limits: {
    title: "Your limits on new entries",
    sector: "One bucket's exposure: {{value}}% of capital",
    risk: "One bucket's open risk: {{value}}% of capital",
    positions: "Open positions: {{value}}",
    none: "No limits set (0 = no limit).",
    over: "at or above",
  },
  notes: {
    betaUnknown: "Beta unknown (counted as 1): {{names}}",
    notWeighted: "Options and structures are not beta-weighted: {{names}}",
    atEntry: "Valued at the entry price (no mark yet): {{names}}",
    oneSided: "One-sided book",
  },
  footer: "Numbers of your own PAPER book, for your own decision. Not a recommendation; nothing here places or closes an order.",
} as const;
