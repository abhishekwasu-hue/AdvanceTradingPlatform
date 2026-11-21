import { ApiError, getToken, request, tryRefresh } from "../api/client";
import { API_V1 } from "../api/base";
import { apiErrorFrom, NETWORK_MESSAGE } from "../api/errors";
import type {
  ChecklistItem, DisciplineSettings, EntryKind, JournalAttachment, JournalEntryFull, JournalFilters, JournalList, JournalSettingsResponse,
  ImportMapping, ImportPreview, ImportResult, JournalStatsResponse, JournalTag, JournalWeek, JournalWeekV2, ManualTradeBody, TagStatus,
  TradeChart, WeeklyReview, BrokerSyncResult, Checkin, CheckinBody, DailyCard, MonthlyReview, WaitingCheckin, WeeklyExtras,
  GoalProgress, JournalDelivery, JournalPlan, JournalToday, PlanBody, ProofPage,
} from "./types";

/** The query string of the journal filters (empty values left out; manual entries are in unless asked otherwise). */
export function filtersQuery(f: JournalFilters, extra: Record<string, string> = {}): string {
  const q = new URLSearchParams(extra);
  if (f.setup) q.set("setup", f.setup);
  if (f.tag) q.set("tag", f.tag);
  if (f.outcome) q.set("outcome", f.outcome);
  if (f.date_from) q.set("date_from", f.date_from);
  if (f.date_to) q.set("date_to", f.date_to);
  if (f.include_manual === false) q.set("include_manual", "false");
  const text = q.toString();
  return text ? `?${text}` : "";
}

/** A server path from a payload ("/api/journal/...") as a path under the client's API root ("/journal/..."). */
export function apiPath(serverPath: string): string {
  return serverPath.replace(/^\/api(?:\/v1)?(?=\/)/, "");
}

const entryBase = (kind: EntryKind, id: number) => `/journal/${kind === "trade" ? "trades" : "manual"}/${id}`;

/** A request that is not JSON (an upload, an image, a file): the same token and silent refresh as `request`. */
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

/** Z6: `/api/journal/*` - the trader's own journal (behind the `discipline_engine` flag; 503 while it is off). */
export const journalApi = {
  week: (week?: string) => request<JournalWeek>(`/journal/week${week ? `?week=${encodeURIComponent(week)}` : ""}`),
  weekV2: (week?: string) => request<JournalWeekV2>(`/journal/week${week ? `?week=${encodeURIComponent(week)}` : ""}`),
  review: (week: string | undefined, narrate = false) => {
    const q = new URLSearchParams({ history: "4" });
    if (week) q.set("week", week);
    if (narrate) q.set("narrate", "true");
    return request<WeeklyReview>(`/journal/review?${q.toString()}`);
  },
  decideTag: (tagId: number, status: TagStatus) =>
    request<JournalTag>(`/journal/tags/${tagId}`, { method: "PATCH", body: JSON.stringify({ status }) }),
  addTag: (tradeId: number, tag: string, note?: string) =>
    request<JournalTag>(`/journal/trades/${tradeId}/tags`, { method: "POST", body: JSON.stringify({ tag, note }) }),
  settings: () => request<JournalSettingsResponse>("/journal/settings"),
  saveSettings: (values: Partial<DisciplineSettings>) =>
    request<JournalSettingsResponse>("/journal/settings", { method: "PUT", body: JSON.stringify(values) }),

  // M62 JN-3: the list, the statistics and the export (the same filters)
  entries: (f: JournalFilters, limit = 200) => request<JournalList>(`/journal/entries${filtersQuery(f, { limit: String(limit) })}`),
  stats: (f: JournalFilters) => request<JournalStatsResponse>(`/journal/stats${filtersQuery(f)}`),
  exportFile: async (format: "csv" | "pdf", f: JournalFilters): Promise<{ blob: Blob; filename: string }> => {
    const response = await authed(`/journal/export${filtersQuery(f, { format })}`);
    const match = /filename="([^"]+)"/.exec(response.headers.get("content-disposition") ?? "");
    return { blob: await response.blob(), filename: match?.[1] ?? `journal.${format}` };
  },

  // M62 JN-2: one entry and the trader's own layer on it
  entry: (kind: EntryKind, id: number) => request<JournalEntryFull>(entryBase(kind, id)),
  saveNotes: (kind: EntryKind, id: number, notes: string) =>
    request<{ notes: string | null }>(`${entryBase(kind, id)}/notes`, { method: "PATCH", body: JSON.stringify({ notes }) }),
  saveUserTags: (kind: EntryKind, id: number, tags: string[]) =>
    request<{ user_tags: string[] }>(`${entryBase(kind, id)}/user-tags`, { method: "PATCH", body: JSON.stringify({ tags }) }),
  saveChecklist: (kind: EntryKind, id: number, items: { rule: string; followed: boolean | null }[]) =>
    request<{ checklist: ChecklistItem[] }>(`${entryBase(kind, id)}/checklist`, { method: "PATCH", body: JSON.stringify({ items }) }),
  attach: async (kind: EntryKind, id: number, file: Blob, name = "image.png"): Promise<JournalAttachment> => {
    const form = new FormData();
    form.append("file", file, name);
    return (await authed(`${entryBase(kind, id)}/attachments`, { method: "POST", body: form })).json() as Promise<JournalAttachment>;
  },
  removeAttachment: (attachmentId: number) => request<{ deleted: number }>(`/journal/attachments/${attachmentId}`, { method: "DELETE" }),
  /** An image of his own (an attachment or a chart snapshot) as a Blob: an <img> cannot send the token. */
  image: async (serverPath: string): Promise<Blob> => (await authed(apiPath(serverPath))).blob(),
  addManual: (body: ManualTradeBody) => request<JournalEntryFull>("/journal/manual", { method: "POST", body: JSON.stringify(body) }),
  removeManual: (id: number) => request<{ deleted: number }>(`/journal/manual/${id}`, { method: "DELETE" }),

  // M88: import outside-ATP trades from a file (a preview first; nothing is stored until Import), each on its own chart
  importPreview: async (file: Blob, name: string, mapping?: ImportMapping): Promise<ImportPreview> =>
    (await authed("/journal/import/preview", { method: "POST", body: importForm(file, name, mapping) })).json() as Promise<ImportPreview>,
  importFile: async (file: Blob, name: string, mapping?: ImportMapping): Promise<ImportResult> =>
    (await authed("/journal/import", { method: "POST", body: importForm(file, name, mapping) })).json() as Promise<ImportResult>,
  tradeChart: (serverPath: string) => request<TradeChart>(apiPath(serverPath)),
  // P2-8 J1-1: today's trades from his own broker sessions (read-only), journalled once
  brokerSync: () => request<BrokerSyncResult>("/journal/broker-sync", { method: "POST" }),

  // P2-8 J2: the 20-second check-in (every field optional; only his own routes read it)
  checkin: (kind: EntryKind, id: number) => request<Checkin>(`${entryBase(kind, id)}/checkin`),
  saveCheckin: (kind: EntryKind, id: number, body: CheckinBody) =>
    request<Checkin>(`${entryBase(kind, id)}/checkin`, { method: "PUT", body: JSON.stringify(body) }),
  addCheckinNote: (kind: EntryKind, id: number, text: string) =>
    request<Checkin>(`${entryBase(kind, id)}/checkin/notes`, { method: "POST", body: JSON.stringify({ text }) }),
  dismissCheckin: (kind: EntryKind, id: number, moment: "open" | "close") =>
    request<{ dismissed: string }>(`${entryBase(kind, id)}/checkin/dismiss`, { method: "POST", body: JSON.stringify({ moment }) }),
  waitingCheckins: () => request<{ waiting: WaitingCheckin[] }>("/journal/checkins/waiting"),

  // P2-8 J3: daily / weekly / monthly reviews and his reflection answers
  daily: (day?: string) => request<DailyCard>(`/journal/daily${day ? `?day=${day}` : ""}`),
  weeklyExtras: (week?: string) => request<WeeklyExtras>(`/journal/review/extras${week ? `?week=${encodeURIComponent(week)}` : ""}`),
  monthly: (month?: string) => request<MonthlyReview>(`/journal/monthly${month ? `?month=${month}` : ""}`),
  saveReflection: (period: string, answer: string) =>
    request<{ period: string; answer: string | null }>("/journal/reflection", { method: "PUT", body: JSON.stringify({ period, answer }) }),

  // P2-8 J4: My proof (facts from his trades + his own entries; pins)
  proof: () => request<ProofPage>("/journal/proof"),
  addProof: (text: string, happenedOn?: string) =>
    request<{ id: number; key: string }>("/journal/proof", { method: "POST", body: JSON.stringify({ text, happened_on: happenedOn || null }) }),
  pinProof: (key: string, pinned: boolean) =>
    request<{ key: string; pinned: boolean }>("/journal/proof/pin", { method: "PUT", body: JSON.stringify({ key, pinned }) }),
  removeProof: (id: number) => request<{ deleted: number }>(`/journal/proof/${id}`, { method: "DELETE" }),

  // P2-8 J5: his if-then plans and process goals; J6: the Today tab
  plans: () => request<{ plans: JournalPlan[]; goals: GoalProgress[] }>("/journal/plans"),
  addPlan: (body: PlanBody) => request<JournalPlan>("/journal/plans", { method: "POST", body: JSON.stringify(body) }),
  changePlan: (id: number, body: Partial<PlanBody>) => request<JournalPlan>(`/journal/plans/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  removePlan: (id: number) => request<{ deleted: number }>(`/journal/plans/${id}`, { method: "DELETE" }),
  today: () => request<JournalToday>("/journal/today"),

  // P2-8 J6: where his reviews go (his own Telegram chat / e-mail; all OFF until he turns them on)
  delivery: async (): Promise<JournalDelivery> => (await request<{ prefs: { journal_delivery: JournalDelivery } }>("/me/prefs")).prefs.journal_delivery,
  saveDelivery: (value: JournalDelivery) =>
    request<{ value: JournalDelivery }>("/me/prefs/journal_delivery", { method: "PUT", body: JSON.stringify(value) }),
};

function importForm(file: Blob, name: string, mapping?: ImportMapping): FormData {
  const form = new FormData();
  form.append("file", file, name);
  if (mapping) form.append("mapping", JSON.stringify(mapping));
  return form;
}

export type JournalApi = typeof journalApi;
