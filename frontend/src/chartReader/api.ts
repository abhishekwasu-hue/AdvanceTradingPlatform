import { request } from "../api/client";
import type { ChatAsk, ChatThread, ChatTurn, ReaderRequest, ReaderResult, ReaderStrategies } from "./types";

/** M86 W3: the chart reader's calls (backend app/chart_reader/routes.py), through the shared `request` (token, refresh). */
const PATH = "/chart-reader";

export const chartReaderApi = {
  /** Labels only (free): the strategies, the default, and the estimated cost of one read. */
  strategies: () => request<ReaderStrategies>(`${PATH}/strategies`),
  /** One read (costs one AI call unless the same chart structure was read before: `cached`). Only on the user's action. */
  read: (body: ReaderRequest) => request<ReaderResult>(`${PATH}/read`, { method: "POST", body: JSON.stringify(body) }),
  /** CA-CHAT: the caller's own chat on his own analysis (free to read). */
  chat: (itemId: number) => request<ChatThread>(`${PATH}/items/${itemId}/chat`),
  /** CA-CHAT: his conditions checked on that analysis's chart (one AI call on his own key; only on his action). */
  ask: (itemId: number, text: string) => request<ChatAsk>(`${PATH}/items/${itemId}/chat`, { method: "POST", body: JSON.stringify({ text }) }),
  clearChat: (itemId: number) => request<{ turns: ChatTurn[] }>(`${PATH}/items/${itemId}/chat`, { method: "DELETE" }),
};
