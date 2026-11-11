/**
 * OC1: the answers of the automatic Option chain page's two reads (backend app/option_chain/chain_view.py).
 * - `GET /api/option-chain/{underlying}/expiries`: the next expiries (IST dates) and the one the page opens on;
 * - `GET /api/option-chain/{underlying}/live?expiry=`: one chain through the trader's own broker session, the summary
 *   and the OI-change state. Every number is the broker's or computed from the broker's; a missing one is null.
 */
export interface ChainExpiry { date: string; kind: "weekly" | "monthly" }

export interface ChainExpiries {
  underlying: string;
  today: string;
  expiries: ChainExpiry[];
  /** The nearest expiry; on an expiry day, the next one. Null when no list is known (the broker's nearest is used). */
  default: string | null;
  source: string;
}

export interface ChainLeg {
  oi: number | null;
  oi_change: number | null;
  ltp: number | null;
  /** Percent (13.2 = 13.2%). */
  iv: number | null;
  volume: number | null;
  bid: number | null;
  ask: number | null;
  delta: number | null;
  gamma: number | null;
  theta: number | null;
  vega: number | null;
}

export interface ChainRow { strike: number; call: ChainLeg; put: ChainLeg }

export interface ChainSummary {
  spot: number | null;
  /** B2b: "last_close" when the market is closed and the spot is the index's last close from our own candles. */
  spot_source?: "broker" | "last_close";
  futures: { symbol: string; expiry: string; ltp: number | null } | null;
  atm_strike: number | null;
  pcr_oi: number | null;
  max_pain: number | null;
  atm_iv: number | null;
  days_to_expiry: number | null;
  max_call_oi_strike: number | null;
  max_put_oi_strike: number | null;
  total_call_oi: number;
  total_put_oi: number;
  /** M96 L1 (Trade port): the PCR of ATM ± 6 strikes (the OI-difference table's window, Trade's banner PCR), two places. */
  pcr_oi_atm?: number | null;
}

export interface OiChangeState {
  /** "ready": shown; "building": the first reading is under a few minutes old; "none": not available. */
  status: "ready" | "building" | "none";
  source: "broker" | "first_reading" | null;
  since: string | null;
}

export interface LiveChain {
  underlying: string;
  expiry: string | null;
  summary: ChainSummary;
  oi_change: OiChangeState;
  rows: ChainRow[];
  fetched_at: string;
  market_open: boolean;
  /** B2a: the chain's capture time while open, the last session close while closed (never the request time). */
  data_as_of?: string;
  /** M96 L1: the Trade chain table's "Net Diff" (backend app/option_chain/net_diff.py). */
  net_diff?: NetDiff;
}

/** One strike of the Trade "Net Diff" table: OI change since the app's first reading of the day (never the exchange's
 *  previous-day change). OI is the broker's whole number. */
export interface NetDiffRow {
  strike: number;
  ce_chg: number;
  ce_oi: number;
  ce_ltp: number | null;
  pe_ltp: number | null;
  pe_oi: number;
  pe_chg: number;
  diff: number;
  dominance: "Bulls" | "Bears" | "Neutral";
}

export interface NetDiff {
  /** "building": this is the day's first reading (every change 0 until the next); "none": no spot / expiry to place it. */
  status: "ready" | "building" | "none";
  /** The day's first reading (the baseline's time). */
  since: string | null;
  step?: number;
  atm?: number;
  window?: number;
  pcr: number | null;
  rows: NetDiffRow[];
  total?: { ce_chg: number; ce_oi: number; pe_oi: number; pe_chg: number; diff: number };
}
