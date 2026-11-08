import { getToken, request, tryRefresh } from "../api/client";
import { API_V1 } from "../api/base";
import { ApiError, apiErrorFrom, NETWORK_MESSAGE } from "../api/errors";
import type { DailyReport, ReportAdmin, ReportTelegram } from "./types";

/** M88-R: `/api/daily-report` (the stored report, behind `ui_v2` + `shared_daily_report`; 404 while off) and its admin card. */
async function pdf(id: number): Promise<{ blob: Blob; filename: string }> {
  const go = () => {
    const token = getToken();
    return fetch(`${API_V1}/daily-report/${id}/pdf`, { credentials: "same-origin", headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}) } });
  };
  let response: Response;
  try {
    response = await go();
    if (response.status === 401 && (await tryRefresh())) response = await go();
  } catch {
    throw new ApiError(0, NETWORK_MESSAGE);
  }
  if (!response.ok) throw await apiErrorFrom(response);
  const match = /filename="([^"]+)"/.exec(response.headers.get("content-disposition") ?? "");
  return { blob: await response.blob(), filename: match?.[1] ?? "daily-market-report.pdf" };
}

const JSON_HEADERS = { "Content-Type": "application/json" };

export const reportApi = {
  latest: () => request<{ report: DailyReport | null }>("/daily-report/latest"),
  pdf,
  admin: () => request<ReportAdmin>("/admin/daily-report"),
  saveSettings: (symbols: string[], recentBars: number) =>
    request<ReportAdmin>("/admin/daily-report/settings", { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ symbols, recent_bars: recentBars }) }),
  telegram: () => request<ReportTelegram>("/daily-report/telegram"),
  saveTelegram: (on: boolean) => request<ReportTelegram>("/daily-report/telegram", { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ telegram: on }) }),
  runNow: () => request<{ started: boolean; trade_date: string }>("/admin/daily-report/run", { method: "POST" }),
};
