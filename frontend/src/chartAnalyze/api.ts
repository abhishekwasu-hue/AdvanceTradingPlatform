import { ApiError, getToken, request, tryRefresh } from "../api/client";
import { API_V1 } from "../api/base";
import { apiErrorFrom, NETWORK_MESSAGE } from "../api/errors";
import type { AnalysisItem, AnalyzeStatus, HybridPick, SharedAnalysis, StrikesAnswer } from "./types";

/**
 * Analyze a chart: the calls the page makes (backend app/chart_analyze/routes.py). JSON routes go through the shared
 * `request`; the upload (multipart) and the image (a Blob) need their own fetch, with the same token and silent
 * refresh, and no JSON content type (the browser sets the multipart boundary).
 */

const PATH = "/chart-analyze";

/** The machine code inside an error's `{"detail": {"message", "code"}}`, or "". */
export function errorCode(e: unknown): string {
  if (!(e instanceof ApiError)) return "";
  try {
    const parsed = JSON.parse(e.body) as { detail?: { code?: unknown } };
    return typeof parsed.detail?.code === "string" ? parsed.detail.code : "";
  } catch {
    return "";
  }
}

/** True when the organisation's flag is off (the platform's standard 503 with X-Feature-Disabled). */
export function isFeatureOff(e: unknown): boolean {
  return e instanceof ApiError && e.status === 503 && /feature is currently disabled/i.test(e.body);
}

async function authed(path: string, init: RequestInit = {}): Promise<Response> {
  const go = () => {
    const token = getToken();
    return fetch(`${API_V1}${path}`, { ...init, credentials: "same-origin", headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
  };
  let response: Response;
  try {
    response = await go();
    if (response.status === 401 && (await tryRefresh())) response = await go();
  } catch {
    throw new ApiError(0, NETWORK_MESSAGE);
  }
  if (!response.ok) throw await apiErrorFrom(response);
  return response;
}

export const chartAnalyzeApi = {
  status: () => request<AnalyzeStatus>(`${PATH}/status`),
  list: () => request<{ items: AnalysisItem[] }>(`${PATH}/items`),
  get: (id: number) => request<AnalysisItem>(`${PATH}/items/${id}`),
  analyze: (id: number) => request<AnalysisItem>(`${PATH}/items/${id}/analyze`, { method: "POST" }),
  /** The option strikes from the user's own settings for this analysis; `symbol` when the picture's symbol was not read. */
  strikes: (id: number, symbol?: string) =>
    request<StrikesAnswer>(`${PATH}/items/${id}/strikes${symbol ? `?symbol=${encodeURIComponent(symbol)}` : ""}`),
  remove: (id: number) => request<{ deleted: number }>(`${PATH}/items/${id}`, { method: "DELETE" }),
  /** A new organisation-only link to the caller's own analysis (an older link stops working). */
  share: (id: number) => request<AnalysisItem>(`${PATH}/items/${id}/share`, { method: "POST" }),
  /** Stops the link at once. */
  unshare: (id: number) => request<AnalysisItem>(`${PATH}/items/${id}/share`, { method: "DELETE" }),
  /** The structure-only copy behind a link (no name, no price, no date, no picture). */
  shared: (token: string) => request<SharedAnalysis>(`${PATH}/shared/${encodeURIComponent(token)}`),
  /** One chart: a file or a link (never both). Hybrid also sends the symbol / timeframe the user picked (M59 CR-4) -
   * the only source of them; no price is ever sent or read from the picture. */
  add: async (source: { file?: Blob; fileName?: string; link?: string }, mode: "image_only" | "hybrid", pick?: HybridPick): Promise<AnalysisItem> => {
    const form = new FormData();
    form.append("mode", mode);
    if (mode === "hybrid" && pick) {
      form.append("symbol", pick.symbol);
      form.append("timeframe", pick.timeframe);
    }
    if (source.file) form.append("file", source.file, source.fileName ?? "chart.png");
    else form.append("link", source.link ?? "");
    const response = await authed(`${PATH}/items`, { method: "POST", body: form });
    return response.json() as Promise<AnalysisItem>;
  },
  /** The stored (cleaned) image of the caller's own item, as a Blob (an <img> cannot send the token). */
  image: async (id: number): Promise<Blob> => (await authed(`${PATH}/items/${id}/image`)).blob(),
  /** A hybrid item's annotated chart of ATP's reading (the download), as a Blob. */
  readingPng: async (id: number): Promise<Blob> => (await authed(`${PATH}/items/${id}/reading.png`)).blob(),
};
