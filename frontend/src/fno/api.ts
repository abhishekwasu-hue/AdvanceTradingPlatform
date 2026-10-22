/**
 * FNO-1: the option chain's trading reads and the ONE order ticket (backend app/paper/fno_routes.py, behind `ui_v2` and
 * the PAPER account). PAPER only: there is no mode in any body, and the only order these calls can lead to is one PAPER
 * order through the ordinary pipeline. Exits use the existing `api.exitNow` (never blocked, ADR-0004).
 */
import { request } from "../api/client";

export type Side = "BUY" | "SELL";
export type Right = "CE" | "PE";
export type OrderType = "MARKET" | "LIMIT" | "SL" | "SL-M";
export type Product = "MIS" | "NRML";

export interface FnoContract {
  underlying: string;
  tradingsymbol: string;
  exchange: string;
  instrument_key: string;
  expiry: string;
  strike: number;
  right: Right;
  lot_size: number;
  tick_size: number;
  /** "NIFTY 28 OCT 22800 CE" */
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
  strike: number;
  right: Right;
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
  risk: { risk_per_trade?: number; allowed_lots?: number };
  errors: string[];
  refusals: string[];
  warnings: string[];
  can_place: boolean;
  confirm_label: string | null;
  as_of: string;
}

export interface OrderResult extends OrderPreview {
  status: "PLACED" | "REFUSED" | "FAILED";
  reasons: string[];
  order_id: number | null;
  trade_id: number | null;
  filled: { price: number; quantity: number; lots: number } | null;
}

const q = (params: Record<string, string | number>) => new URLSearchParams(Object.entries(params).map(([k, v]) => [k, String(v)])).toString();

export const fnoApi = {
  contract: (underlying: string, expiry: string, strike: number, right: Right) =>
    request<FnoContract>(`/paper/fno/contract?${q({ underlying, expiry, strike, right })}`),
  held: (underlying: string, expiry: string) => request<HeldLeg[]>(`/paper/fno/held?${q({ underlying, expiry })}`),
  preview: (ask: OrderAsk) => request<OrderPreview>("/paper/fno/order/preview", { method: "POST", body: JSON.stringify(ask) }),
  place: (ask: OrderAsk) => request<OrderResult>("/paper/fno/order", { method: "POST", body: JSON.stringify(ask) }),
};
export type FnoApi = typeof fnoApi;
