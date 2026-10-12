// N2-1: ladder evidence payloads as the server builds them (app/decision_tools/own_stats.evidence), for tests and
// stories. OK: 12 wins of +200 and 8 losses of -100, capital 10,000, ruin at 10 %. LOSING: 5 x +100, 15 x -200.
import type { LadderEvidence } from "../types";

const BASIS = "Your closed PAPER trades priced from platform quotes, net P&L after charges";
const FORMULA = "f = (p·b − q) / (b·(p + q))";
const KELLY_NOTE = "A formula's reading of past outcomes, not a position size; sizing stays with your risk cap";

export const OK: LadderEvidence = {
  trades: 20, wins: 12, losses: 8, flat: 0, min_trades: 20, enough: true, basis: BASIS, status: "ok",
  net: 1600, win_rate: 0.6, avg_win: 200, avg_loss: 100, payoff_ratio: 2, expectancy: 80, profit_factor: 3,
  kelly: { fraction: 0.4, status: "ok", formula: FORMULA, note: KELLY_NOTE },
  ruin: { probability: 0.0004011, status: "ok", ruin_pct: 10, distance: 1000, method: "lundberg_upper_bound",
          note: "Upper bound on ever falling 10% below the starting capital if trades kept coming from your past results at the same rupee sizes" },
};

export const LOSING: LadderEvidence = {
  trades: 20, wins: 5, losses: 15, flat: 0, min_trades: 20, enough: true, basis: BASIS, status: "ok",
  net: -2500, win_rate: 0.25, avg_win: 100, avg_loss: 200, payoff_ratio: 0.5, expectancy: -125, profit_factor: 0.167,
  kelly: { fraction: -1.25, status: "negative", formula: FORMULA, note: KELLY_NOTE },
  ruin: { probability: 1, status: "certain", ruin_pct: 50, distance: 5000, method: "lundberg_upper_bound",
          note: "Upper bound on ever falling 50% below the starting capital if trades kept coming from your past results at the same rupee sizes" },
};

export const TOO_FEW: LadderEvidence = {
  trades: 2, wins: 1, losses: 1, flat: 0, min_trades: 20, enough: false, basis: BASIS, status: "too_few",
  note: "2 of 20 closed trades: no figures yet",
};
