import { request } from "../api/client";
import type { ReaderRequest, ReaderResult, ReaderStrategies } from "./types";

/** M86 W3: the chart reader's calls (backend app/chart_reader/routes.py), through the shared `request` (token, refresh). */
const PATH = "/chart-reader";

export const chartReaderApi = {
  /** Labels only (free): the strategies, the default, and the estimated cost of one read. */
  strategies: () => request<ReaderStrategies>(`${PATH}/strategies`),
  /** One read (costs one AI call unless the same chart structure was read before: `cached`). Only on the user's action. */
  read: (body: ReaderRequest) => request<ReaderResult>(`${PATH}/read`, { method: "POST", body: JSON.stringify(body) }),
};
