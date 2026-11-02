/**
 * SE-7 (flag smart_execution): an incomplete multi-leg order waiting for his answer (backend
 * app/execution/incomplete_structures.py `view_for_user`, routes app/execution/incomplete_routes.py). Its own module:
 * the F&O ticket's api.ts stays PAPER-only, while an incomplete order may be one of his bots' (any mode).
 */
import { request } from "../api/client";
import type { Side } from "./api";

const post = (body: unknown) => ({ method: "POST", body: JSON.stringify(body) });
export type IncompleteAction = "complete_rest" | "close_filled" | "keep";
export type IncompleteState = "INCOMPLETE" | "COMPLETING" | "COMPLETING_SENT" | "CLOSING" | "CLOSED" | "KEPT" | "COMPLETED";
export interface IncompleteLeg { trade_id: number; symbol: string; side: Side; role: "hedge" | "sold"; quantity: number; entry_price: number | null; open: boolean }
/** `exchange` (M76 MP-9): where the leg trades, for its new last price in the workspace (absent on an older server). */
export interface IncompletePending { symbol: string; side: Side; role: "hedge" | "sold"; quantity: number; exchange?: string }

/** M76 MP-9: what of this leg already filled (a part fill leaves its remainder pending under the same contract and side). */
export function filledOf(item: Pick<IncompleteItem, "filled">, leg: Pick<IncompletePending, "symbol" | "side">): number {
  return item.filled.filter((f) => f.symbol === leg.symbol && f.side === leg.side).reduce((a, f) => a + f.quantity, 0);
}
export interface IncompleteItem {
  group_id: string; underlying: string; strategy: string; mode: "PAPER" | "LIVE"; state: IncompleteState; message: string; failure: string;
  deadline: string | null; seconds_left: number | null; on_no_answer: "close_filled" | "keep" | null; rule: string | null;
  filled: IncompleteLeg[]; pending: IncompletePending[]; actions: IncompleteAction[];
}
export interface IncompleteView { items: IncompleteItem[]; as_of: string }
export interface IncompleteAnswer { state: IncompleteState | null; group_id: string; message: string }
export const incompleteApi = {
  list: () => request<IncompleteView>("/trading/incomplete"),
  answer: (groupId: string, action: IncompleteAction) =>
    request<IncompleteAnswer>(`/trading/incomplete/${encodeURIComponent(groupId)}/answer`, post({ action })),
};
export type IncompleteApi = typeof incompleteApi;

/** Owner fix T1-4 (24 Oct): the two choices in his words - "Complete the leg" / "Exit the filled leg" (plural with more
 *  than one). A short is never left uncovered without this alert. */
export function completeLabel(pending: number): string { return pending === 1 ? "Complete the leg" : "Complete the legs"; }
export function exitFilledLabel(filled: number): string { return filled === 1 ? "Exit the filled leg" : "Exit the filled legs"; }
