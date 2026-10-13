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
  experience: {
    title: "Experience level",
    intro: "Your level comes from your own PAPER record and moves only with it. Each level opens more complex entries.",
    levelsLabel: "Experience levels",
    current: "current",
    record: "Your record: {{trades}} closed trades over {{sessions}} sessions, largest drawdown {{dd}}%.",
    next: "{{level}} opens {{opens}}:",
    allReached: "Every level is reached.",
    gatesLabel: "Gates",
    gate: {
      allowed: "Open at your level",
      optedOut: "Opted out for today (logged)",
      shut: "Opens at {{level}}",
    },
    optOut: {
      open: "Opt out for today",
      reasonLabel: "Why (at least {{min}} characters, kept in your record)",
      confirm: "Opt out of {{gate}} until the end of today (logged)",
      cancel: "Cancel",
      done: "Opted out of {{gate}} for today. It is logged and counted.",
      caveat: "An opt-out lifts this gate for your own new entries until the day ends (IST). It is logged and counted below.",
    },
    counts: "Opt-outs: {{today}} today · {{week}} in the last 7 days · {{total}} in all",
    recentLabel: "Recent opt-outs",
    recentRow: "{{day}} · {{gate}} at {{level}}: {{reason}}",
  },
  discipline: {
    weeksLabel: "Weekly discipline scores",
    weekScore: "{{week}}: {{score}}",
    needed: "{{have}} of {{needed}} weeks scored",
    engineOff: "The journal's discipline engine is off for your organisation.",
  },
} as const;
