// N2-2: an experience status as the server builds it (app/paper/experience.status) for a Beginner with one opt-out of
// futures today - for tests and stories.
import type { ExperienceStatus } from "../types";

export const BEGINNER_STATUS: ExperienceStatus = {
  level: "BEGINNER", level_label: "Beginner",
  levels: [
    { key: "BEGINNER", label: "Beginner", reached: true, opens: [], criteria: [] },
    { key: "INTERMEDIATE", label: "Intermediate", reached: false, opens: ["Futures"], criteria: [
      { key: "closed_trades", label: "Closed PAPER trades", value: 12, target: 30, comparison: "at_least", met: false },
      { key: "sessions", label: "Sessions with a PAPER trade", value: 8, target: 20, comparison: "at_least", met: false },
      { key: "max_drawdown_pct", label: "Largest drawdown, % of peak equity", value: 3.2, target: 10, comparison: "at_most", met: true },
    ] },
    { key: "ADVANCED", label: "Advanced", reached: false, opens: ["Naked short options"], criteria: [
      { key: "closed_trades", label: "Closed PAPER trades", value: 12, target: 100, comparison: "at_least", met: false },
      { key: "sessions", label: "Sessions with a PAPER trade", value: 8, target: 60, comparison: "at_least", met: false },
      { key: "max_drawdown_pct", label: "Largest drawdown, % of peak equity", value: 3.2, target: 10, comparison: "at_most", met: true },
    ] },
  ],
  record: { closed_trades: 12, sessions: 8, max_drawdown_pct: 3.2 },
  gates: [
    { key: "futures", label: "Futures", opens_at: "INTERMEDIATE", opens_at_label: "Intermediate", allowed_by_level: false, opted_out_today: true, allowed: true },
    { key: "naked_short_options", label: "Naked short options", opens_at: "ADVANCED", opens_at_label: "Advanced", allowed_by_level: false, opted_out_today: false, allowed: false },
  ],
  opt_outs: { today: 1, last_7_days: 2, total: 5, recent: [
    { gate: "futures", gate_label: "Futures", level: "BEGINNER", reason: "Practising index futures on paper today", day: "2026-10-12" },
  ] },
  min_reason_chars: 15, day: "2026-10-12", exits: "Exits are never gated: stops, targets, time exits and Exit now always work.",
  as_of: "2026-10-12T10:00:00+00:00",
};
