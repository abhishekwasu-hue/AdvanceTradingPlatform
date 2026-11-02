import { getToken, request, tryRefresh } from "../api/client";
import { API_V1 } from "../api/base";
import { ApiError, apiErrorFrom, NETWORK_MESSAGE } from "../api/errors";
import type { DownloadFormat, SaveStudyBody, StudyList, StudySummary } from "./model";

/** M77 SX-7: `/api/studies` - his own saved studies (behind `ui_v2` + `study_export`; 404 while either is off). */
async function authed(path: string): Promise<Response> {
  const go = () => {
    const token = getToken();
    return fetch(`${API_V1}${path}`, { credentials: "same-origin", headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
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

export interface StudyQuery { symbol?: string; dateFrom?: string; dateTo?: string }

export const studyApi = {
  save: (body: SaveStudyBody) => request<StudySummary>("/studies", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  list: (q: StudyQuery = {}) => {
    const p = new URLSearchParams();
    if (q.symbol?.trim()) p.set("symbol", q.symbol.trim());
    if (q.dateFrom) p.set("date_from", q.dateFrom);
    if (q.dateTo) p.set("date_to", q.dateTo);
    const qs = p.toString();
    return request<StudyList>(`/studies${qs ? `?${qs}` : ""}`);
  },
  remove: (id: number) => request<{ deleted: boolean }>(`/studies/${id}`, { method: "DELETE" }),
  /** The file of one study (rebuilt by the server from the stored study; the same study gives the same file). */
  download: async (id: number, format: DownloadFormat, opts: { tf?: string; cvd?: boolean } = {}): Promise<{ blob: Blob; filename: string }> => {
    const p = new URLSearchParams();
    if (format === "png-one" && opts.tf) p.set("tf", opts.tf);
    if ((format === "pdf" || format === "png" || format === "png-one") && opts.cvd) p.set("cvd", "true");
    const kind = format === "png-one" ? "png" : format;
    const qs = p.toString();
    const response = await authed(`/studies/${id}/${kind}${qs ? `?${qs}` : ""}`);
    const match = /filename="([^"]+)"/.exec(response.headers.get("content-disposition") ?? "");
    return { blob: await response.blob(), filename: match?.[1] ?? `study.${kind === "csv" ? "zip" : kind}` };
  },
};
