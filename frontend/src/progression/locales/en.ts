/**
 * N2 progression and decision-tool strings (namespace `progression`, locale `en`). English only: the Devanagari check
 * and i18n.test.ts keep it so. Facts only - no advice wording, no profit claims (i18n.test.ts scans every value).
 * Interpolation: {{name}}.
 */
export const PROGRESSION_EN = {
  evidence: {
    title: "Your figures",
    basis: "From your own closed PAPER trades, net of charges. Information only: nothing here sizes or places a trade.",
    tooFew: "{{count}} of {{needed}} closed trades - the figures appear at {{needed}}.",
    counts: "{{trades}} trades · {{wins}} won · {{losses}} lost · {{flat}} flat",
    winRate: "Win rate",
    expectancy: "Expectancy per trade",
    profitFactor: "Profit factor",
    payoffRatio: "Average win / average loss",
    avgWinLoss: "Average win {{win}} · average loss {{loss}}",
    noLoss: "no losing trade",
    kelly: {
      label: "Kelly reference",
      ok: "{{pct}} of capital lost on a losing trade maximises the long-run growth of these past outcomes ({{formula}}).",
      negative: "Below zero ({{pct}}): these past outcomes lost money on average.",
      no_losses: "Not defined: no losing trade yet.",
      no_wins: "Not defined: no winning trade yet.",
      caveat: "A formula's reading of the past, not a position size. Sizing stays with your risk cap.",
    },
    ruin: {
      label: "Risk of ruin (upper bound)",
      ok: "At most {{pct}} chance of ever falling {{ruinPct}}% ({{distance}}) below your starting capital.",
      certain: "Certain in this model: with these outcomes a fall of {{ruinPct}}% ({{distance}}) below the start comes sooner or later.",
      no_losses: "Not bounded: no losing trade yet.",
      caveat: "If trades kept coming from your past results at the same rupee sizes (Lundberg bound).",
    },
  },
  discipline: {
    weeksLabel: "Weekly discipline scores",
    weekScore: "{{week}}: {{score}}",
    needed: "{{have}} of {{needed}} weeks scored",
    engineOff: "The journal's discipline engine is off for your organisation.",
  },
} as const;
