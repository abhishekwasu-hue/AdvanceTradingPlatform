/**
 * FNO-1: the option chain's trading reads and the ONE order ticket (backend app/paper/fno_routes.py, behind `ui_v2` and
 * the PAPER account). PAPER only: there is no mode in any body, and the only order these calls can lead to is one PAPER
 * order through the ordinary pipeline. Exits use the existing `api.exitNow` (never blocked, ADR-0004).
 */
import { request } from "../api/client";
import type { Evaluation, Leg as BuilderLeg } from "../optionsBuilder/model";
import type { RiskBlock } from "../settings/riskLimits";

export type Side = "BUY" | "SELL";
export type Right = "CE" | "PE";
/** FNO-3: what the ticket and the basket take - an option's right, or "FUT" for a future (which has no strike). */
export type ContractRight = Right | "FUT";
export type OrderType = "MARKET" | "LIMIT" | "SL" | "SL-M";
export type Product = "MIS" | "NRML";

export interface FnoContract {
  underlying: string;
  tradingsymbol: string;
  exchange: string;
  instrument_key: string;
  expiry: string;
  /** null for a future */
  strike: number | null;
  right: ContractRight;
  lot_size: number;
  tick_size: number;
  /** "NIFTY 28 OCT 22800 CE", a future "NIFTY 28 OCT FUT" */
  label: string;
  /** Not in the instrument master yet (null). */
  freeze_quantity: number | null;
}

/** One of the caller's open PAPER option positions on the chain on screen ("P"). */
export interface HeldLeg {
  trade_id: number;
  tradingsymbol: string;
  strike: number;
  right: Right;
  side: Side;
  quantity: number;
  lots: number;
  lot_size: number;
  entry_price: number;
  mark_price: number | null;
  pnl: number | null;
  stop_loss: number | null;
}

export interface OrderAsk {
  underlying: string;
  expiry: string;
  /** null for a future */
  strike: number | null;
  right: ContractRight;
  side: Side;
  lots: number;
  order_type: OrderType;
  product: Product;
  price?: number | null;
  trigger_price?: number | null;
  stop_loss?: number | null;
  chain_ltp?: number | null;
  client_ref?: string | null;
}

export interface MoneyLine { value: number | null; basis: string; estimated?: boolean; note?: string }

export interface OrderPreview {
  mode: "PAPER";
  practice: string;
  contract: FnoContract | null;
  label: string;
  side: Side;
  lots: number;
  lot_size: number | null;
  units: number | null;
  order_type: OrderType;
  product: Product;
  price: number | null;
  price_source: "live" | "chain" | null;
  fill_price: number | null;
  fill_note: string | null;
  stop_loss: number | null;
  margin: MoneyLine | null;
  max_loss: MoneyLine | null;
  charges: { total: number; estimated: boolean } | null;
  /** M64 RM-3: `blocks` - each Risk limits refusal as a block message (sentence, link, the smaller size offered). */
  risk: { risk_per_trade?: number; allowed_lots?: number; blocks?: RiskBlock[] };
  errors: string[];
  refusals: string[];
  warnings: string[];
  can_place: boolean;
  confirm_label: string | null;
  as_of: string;
  /** TM Part 7 (flag smart_execution): the exchange's freeze limit and the slices (lots) the order goes in. */
  smart?: { freeze_quantity: number | null; slices: number[]; note: string | null } | null;
}

export interface OrderResult extends OrderPreview {
  status: "PLACED" | "REFUSED" | "FAILED";
  reasons: string[];
  order_id: number | null;
  trade_id: number | null;
  filled: { price: number; quantity: number; lots: number } | null;
}

const q = (params: Record<string, string | number | null>) =>
  new URLSearchParams(Object.entries(params).filter(([, v]) => v !== null).map(([k, v]) => [k, String(v)])).toString();

export const fnoApi = {
  /** An option (`right` CE / PE with its strike) or a future (`right` FUT, strike null). */
  contract: (underlying: string, expiry: string, strike: number | null, right: ContractRight) =>
    request<FnoContract>(`/paper/fno/contract?${q({ underlying, expiry, strike: right === "FUT" ? null : strike, right })}`),
  held: (underlying: string, expiry: string) => request<HeldLeg[]>(`/paper/fno/held?${q({ underlying, expiry })}`),
  preview: (ask: OrderAsk) => request<OrderPreview>("/paper/fno/order/preview", { method: "POST", body: JSON.stringify(ask) }),
  place: (ask: OrderAsk) => request<OrderResult>("/paper/fno/order", { method: "POST", body: JSON.stringify(ask) }),
  // M45 MA-6 (`ui_v2` + `multi_account`): the user's simulated PAPER accounts, one order on several of them, one exit.
  practiceAccounts: () => request<PracticeAccount[]>("/accounts/mine/paper"),
  placeMulti: (ask: OrderAsk, runOn: string) =>
    request<MultiResult>("/accounts/mine/paper/order", { method: "POST", body: JSON.stringify({ order: ask, run_on: runOn }) }),
  addPracticeAccount: (label: string, capital: number) =>
    request<PracticeAccount>("/accounts/mine/paper", { method: "POST", body: JSON.stringify({ label, capital }) }),
  changePracticeAccount: (id: number, patch: Partial<{ role: string; clear_role: boolean; group_name: string; enabled: boolean;
    sizing_mode: string; sizing_value: number; capital: number; confirm: boolean }>) =>
    request<PracticeAccount>(`/accounts/mine/paper/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
  exitMulti: (ref: string) => request<MultiExitResult>("/accounts/mine/paper/exit", { method: "POST", body: JSON.stringify({ ref }) }),
};

/** M45: one simulated PAPER account of the user (GET /api/accounts/mine/paper). */
export interface PracticeAccount {
  id: number;
  label: string;
  capital: number;
  role: "PRIMARY" | "SECONDARY" | null;
  group_name: string | null;
  enabled: boolean;
  sizing_mode: "FIXED" | "MULTIPLIER" | "PCT_CAPITAL";
  sizing_value: number;
}

export interface MultiRow { account_id: number; label: string; status: string; lots: number; trade_id: number | null; reasons: string[] }
export interface MultiResult { why: string; override: string | null; results: MultiRow[] }
export interface MultiExitResult { ref: string; results: { trade_id: number; account_id: number | null; label: string | null; status: string; reasons: string[] }[] }

/** The "Run on" choices for the user's simulated accounts: one order as today, the Primary, the Primary with one
 * group, or every account with a role (the server resolves and sizes; switched-off accounts are skipped with a reason). */
export function runOnOptions(accounts: readonly PracticeAccount[]): { value: string; label: string }[] {
  if (!accounts.some((a) => a.role === "PRIMARY")) return [];
  const groups = [...new Set(accounts.filter((a) => a.role === "SECONDARY" && a.group_name).map((a) => a.group_name as string))].sort();
  return [
    { value: "ONE", label: "This order only" },
    { value: "PRIMARY", label: "Primary account" },
    ...groups.map((g) => ({ value: `PRIMARY+${g}`, label: `Primary + ${g}` })),
    { value: "ALL", label: "All accounts" },
  ];
}
export type FnoApi = typeof fnoApi;

// --- FNO-2: the basket (backend app/paper/fno_basket.py). Each leg placed is one PAPER order through the same ticket. ---

/** One leg as the basket routes take it (prices per unit; `iv` a fraction, 0.14 = 14 %). */
export interface BasketLegAsk {
  underlying: string;
  expiry: string;
  /** null for a future (FNO-3) */
  strike: number | null;
  right: ContractRight;
  side: Side;
  lots: number;
  price?: number | null;
  stop_loss?: number | null;
  iv?: number | null;
}

export interface BasketAsk { legs: BasketLegAsk[]; spot?: number | null; product?: Product }
/** TERM-2 step 4: the evaluate call only - `with_open` (R2 what-if, flag term_whatif) and `margin_pair` (R4, flag
 *  term_margin_pair). The server ignores each while its flag is off; the preview and execute bodies never carry them. */
export interface EvaluateAsk extends BasketAsk { with_open?: boolean; margin_pair?: boolean }

export interface MarginLine { value: number | null; estimated: true; basis: string; note?: string }
export interface HedgeMargin {
  with_hedge: MarginLine;
  without_hedge: MarginLine | null;
  benefit: number | null;
  hedged: boolean;
  source: "estimate";
  note: string;
}

/** TERM-2 R2: the combined payoff of his own open PAPER legs (same underlying + expiry) and the basket's new legs - the
 *  same engine, one server call. `open_legs` are the open legs as the payoff chart draws them (ids "open-<trade id>"). */
export interface WhatIfOpen { trade_id: number; label: string; side: Side; lots: number; price: number | null }
export type WhatIf =
  | (Evaluation & { ok: true; errors: string[]; open: WhatIfOpen[]; open_legs: Omit<BuilderLeg, "premium_source" | "priced_for">[] })
  | { ok: false; open: WhatIfOpen[]; errors: string[] };

/** TERM-2 R4: margin required (the broker's basket margin through his own session, else ATP's labelled estimate) and
 *  available (the Terminal status funds). */
export interface MarginPair {
  source: "broker" | "estimate"; estimate: boolean; label: string; required: number | null; without_hedge: number | null;
  benefit: number | null; available: number | null; available_note: string; free_after: number | null; notes: string[];
}

/** The basket's numbers: the Options Builder's evaluation plus the lot sizes, labels and the hedge margin. */
export type BasketNumbers = Evaluation & {
  ok: true; errors: string[]; margin: Evaluation["margin"] & { hedge: HedgeMargin };
  lot_sizes: (number | null)[]; labels: string[]; order: number[];
  whatif?: WhatIf; margin_pair?: MarginPair;
};
export type BasketEvaluation = BasketNumbers | { ok: false; errors: string[] };

export interface BasketSettings { source: string | null; width_steps: number; lots: number; stop_pct: number | null; note: string }   // DEF-0: null = no stop of his own

export interface BasketPreviewLeg extends OrderPreview { seq: number; leg: number; role: "hedge" | "short" }
export interface BasketPreview {
  mode: "PAPER";
  practice: string;
  legs: BasketPreviewLeg[];
  refusals: string[];
  totals: {
    margin: Evaluation["margin"] & { hedge: HedgeMargin };
    extremes: Evaluation["extremes"];
    net_premium: number;
    costs: number;
    breakevens: number[] | null;
  } | null;
  totals_errors: string[];
  /** PAPER-NR: his own limits as information in PAPER (never a refusal); absent on older servers. */
  warnings?: string[];
  can_execute: boolean;
  product: Product;
  confirm_label: string | null;
  as_of: string;
  /** TM Part 7 (flag smart_execution): the order the legs go in, their slices, the margin; null with the flag off. */
  smart?: SmartView | null;
}

/** TM Part 7 smart execution: the confirm step's block (backend app/paper/fno_basket.py `smart_view`). */
export interface SmartSequenceStep { seq: number; leg: number; label: string; side: Side; role: "hedge" | "short"; lots: number; slices: number[]; note: string | null }
export interface SmartMargin {
  source: "broker" | "estimate";
  label: string;
  required: number | null;
  without_hedge: number | null;
  benefit: number | null;
  free_before: number | null;
  free_after: number | null;
  free_source: "broker" | "practice";
  notes: string[];
}
export interface SmartView { sequence: SmartSequenceStep[]; margin: SmartMargin; entry_rule: string; exit_rule: string; incomplete_rule: string }
export type SmartState = "COMPLETE" | "INCOMPLETE" | "NOTHING_PLACED";
export interface CloseFilledResult {
  mode: "PAPER";
  state: SmartState;
  actions: string[];
  message: string;
  legs: { seq: number; trade_id: number; symbol: string; side: Side; status: "CLOSED" | "FAILED" | "NOT_PLACED"; reasons: string[]; exit_price: number | null; pnl: number | null }[];
}
export interface SettlementWarning {
  trade_id: number; label: string; underlying: string; expiry: string; days_to_expiry: number; state: "ITM" | "NEAR_ITM" | "FUTURE";
  delivery_value: number | null; quantity: number; obligation: string; message: string;
}
export interface SettlementView { warnings: SettlementWarning[]; unpriced: string[]; settings: { days_before: number; band_pct: number; telegram: boolean }; note: string; as_of: string }

export type LegStatus = "PLACED" | "REFUSED" | "FAILED" | "NOT_PLACED";
export interface BasketResultLeg {
  seq: number;
  leg: number;
  label: string;
  side: Side;
  lots: number;
  status: LegStatus;
  reasons: string[];
  filled: { price: number; quantity: number; lots: number } | null;
  order_id: number | null;
  trade_id: number | null;
}
export interface BasketResult {
  mode: "PAPER";
  status: "PLACED" | "PARTIAL" | "REFUSED";
  reasons: string[];
  legs: BasketResultLeg[];
  placed: number;
  next_steps: string[];
  /** TM Part 7 (flag smart_execution): the run's state and the user's two choices when it is INCOMPLETE. */
  state?: SmartState;
  actions?: string[];
  message?: string;
  filled_trade_ids?: number[];
  /** SX: the strategy exit set on the filled legs (absent: none was asked for). */
  exits?: StrategyExitResult;
}

/** SX: "Exits for this strategy" as the execute request carries it (backend fno_routes.StrategyExitsBody). */
export interface StrategyExitsAsk {
  measure: "MTM_INR" | "PREMIUM_PCT" | "SPOT";
  sl_value: number | null;
  target_value: number | null;
  trailing?: { kind: "LOCK" | "TRAIL" | "LOCK_TRAIL" | "BREAK_EVEN"; after?: number; lock?: number; step?: number; by?: number } | null;
}
export type StrategyExitResult =
  | { status: "ARMED"; id: number; words: string; legs: number;
      /** F3: what it watches, the stop in force (₹ MTM signed: -X a loss of ₹X) and the target, as numbers. */
      measure?: "MTM_INR" | "SPOT" | "NET_PREMIUM"; stop_now?: number | null; target_value?: number | null }
  | { status: "NOT_SET"; reason: string };

export interface BrokerBasketOrder { tradingsymbol: string; exchange: string; transaction_type: Side; quantity: number }
export type BasketBrokerLink =
  | { available: true; broker: "zerodha"; publisher_api_key: string; orders: BrokerBasketOrder[] }
  | { available: false; why: string };

const post = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) });

export const basketApi = {
  evaluate: (ask: EvaluateAsk) => request<BasketEvaluation>("/paper/fno/basket/evaluate", post(ask)),
  settings: (underlying: string) => request<BasketSettings>(`/paper/fno/basket/settings?${q({ underlying })}`),
  preview: (ask: BasketAsk) => request<BasketPreview>("/paper/fno/basket/preview", post(ask)),
  execute: (ask: BasketAsk & { basket_ref: string; exits?: StrategyExitsAsk | null }) => request<BasketResult>("/paper/fno/basket/execute", post(ask)),
  brokerLink: (ask: BasketAsk) => request<BasketBrokerLink>("/paper/fno/basket/broker-link", post(ask)),
  closeFilled: (trade_ids: number[]) => request<CloseFilledResult>("/paper/fno/basket/close-filled", post({ trade_ids })),
  settlement: () => request<SettlementView>("/paper/fno/settlement"),
};
export type BasketApi = typeof basketApi;
