/**
 * Trading Terminal T1: the server reads the Terminal makes - the status bar (`GET /api/terminal/status`, the caller's own
 * figures), his own positions and orders (`?mine=true` on the existing reads, TT-15), one order's trail, and his layouts
 * (prefs namespace `terminal`). Reads only, plus saving his layouts: no order is placed from here - orders go through
 * the one ticket and basket (src/fno).
 */
import { request } from "../api/client";
import type { TradeRecord } from "../types";
import { NAMESPACE, forSave, readPrefs, type TerminalPrefs } from "./layoutModel";

export type FeedState = "live" | "connecting" | "reconnecting" | "renew_needed" | "polling" | "off" | "none";

export interface TerminalStatus {
  today: { mtm: number; booked: number; note: string };
  open: { count: number; priced: number; mtm: number | null; as_of: string | null };
  /** M68 PR-7: null while his paper starting capital is Not set (`needs_capital`). */
  funds: { balance: number | null; available: number | null; source: string; note: string; needs_capital?: boolean };
  margin: { used: number; estimated: boolean; not_estimated: number; note: string };
  mode: { mode: "PAPER" | "LIVE"; live_allowed: boolean; message: string };
  feed: { state: FeedState; message: string; broker_name?: string };
  kill_switch: { engaged: boolean; scope: "platform" | "account" | null; message: string };
  as_of: string;
}

export interface OrderRow {
  id: number;
  mode: string;
  symbol: string;
  direction: string;
  quantity: number;
  status: string;
  trade_id: number | null;
  reasons: string[];
  created_at: string;
  updated_at: string;
}

export interface OrderEvent { id: number; from_status: string | null; to_status: string; detail: string; created_at: string }

type PrefsRead = { prefs?: Record<string, unknown>; versions?: Record<string, number> };

export interface TerminalApi {
  status: () => Promise<TerminalStatus>;
  positions: () => Promise<TradeRecord[]>;
  orders: () => Promise<OrderRow[]>;
  events: (orderId: number) => Promise<OrderEvent[]>;
  loadLayouts: () => Promise<{ value: TerminalPrefs; version: number }>;
  saveLayouts: (value: TerminalPrefs, version: number) => Promise<{ value: TerminalPrefs; version: number }>;
}

export function terminalApi(fetcher: typeof request = request): TerminalApi {
  return {
    status: () => fetcher<TerminalStatus>("/terminal/status"),
    positions: () => fetcher<TradeRecord[]>("/positions?mine=true"),
    orders: () => fetcher<OrderRow[]>("/orders?mine=true&limit=100"),
    events: (orderId) => fetcher<OrderEvent[]>(`/orders/${orderId}/events`),
    loadLayouts: async () => {
      const r = await fetcher<PrefsRead>("/me/prefs");
      return { value: readPrefs(r.prefs?.[NAMESPACE]), version: r.versions?.[NAMESPACE] ?? 0 };
    },
    saveLayouts: async (value, version) => {
      const r = await fetcher<{ value: unknown; version: number }>(`/me/prefs/${NAMESPACE}?version=${version}`, {
        method: "PUT", body: JSON.stringify(forSave(value)),
      });
      return { value: readPrefs(r.value), version: r.version };
    },
  };
}

export const defaultTerminalApi = terminalApi();

/** Open / Executed / Rejected / Cancelled, from the order's state (app/core/enums.py OrderStatus). REV-1 M3: a cancelled
 * order is its own group, never listed under "Rejected". */
export type OrderGroup = "open" | "executed" | "rejected" | "cancelled";
const OPEN = new Set(["CREATED", "VALIDATING", "RISK_CHECK", "SUBMITTED", "PENDING", "PARTIAL_FILL"]);
const EXECUTED = new Set(["FILLED", "POSITION_OPEN"]);

export function orderGroup(status: string): OrderGroup {
  if (OPEN.has(status)) return "open";
  if (EXECUTED.has(status)) return "executed";
  if (status === "CANCELLED") return "cancelled";
  return "rejected";
}

/** An order state in a trader's words (no internal state names on screen). */
export function statusWords(status: string): string {
  const words: Record<string, string> = {
    CREATED: "Received", VALIDATING: "Being checked", RISK_CHECK: "Risk check", SUBMITTED: "Sent", PENDING: "Waiting to fill",
    PARTIAL_FILL: "Partly filled", FILLED: "Filled", POSITION_OPEN: "Filled, position open", REJECTED: "Refused", FAILED: "Did not go through",
    CANCELLED: "Cancelled",
  };
  return words[status] ?? "Updated";
}
