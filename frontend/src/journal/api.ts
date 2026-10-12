import { request } from "../api/client";
import type { DisciplineSettings, JournalLessons, JournalSettingsResponse, JournalTag, JournalWeek, TagStatus, WeeklyReview } from "./types";

/** Z6: `/api/journal/*` - the trader's own journal (behind the `discipline_engine` flag; 503 while it is off). */
export const journalApi = {
  week: (week?: string) => request<JournalWeek>(`/journal/week${week ? `?week=${encodeURIComponent(week)}` : ""}`),
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
  /** N2-11: learn-from-your-journal cards (404 while `journal_lessons` is off). */
  lessons: (weeks = 8) => request<JournalLessons>(`/journal/lessons?weeks=${weeks}`),
  saveSettings: (values: Partial<DisciplineSettings>) =>
    request<JournalSettingsResponse>("/journal/settings", { method: "PUT", body: JSON.stringify(values) }),
};
