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
export interface IncompletePending { symbol: string; side: Side; role: "hedge" | "sold"; quantity: number }
export interface IncompleteItem {
  group_id: string; underlying: string; strategy: string; mode: "PAPER" | "LIVE"; state: IncompleteState; message: string; failure: string;
  deadline: string | null; seconds_left: number | null; on_no_answer: "close_filled" | "keep"; rule: string | null;
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
