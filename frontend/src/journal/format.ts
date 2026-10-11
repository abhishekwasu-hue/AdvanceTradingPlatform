/** Z6: pure helpers for the Journal page - labels, week arithmetic, number formats. Descriptive words only. */

export const TAG_LABELS: Record<string, string> = {
  revenge_trade: "Revenge trade",
  fomo_entry: "FOMO entry",
  averaging_loser: "Averaging a loser",
  moved_stop_against: "Stop moved against",
  no_stop: "No stop",
  overtrading: "Overtrading",
  early_exit: "Early exit",
  held_through_event: "Held through event",
};

export const TAG_HELP: Record<string, string> = {
  revenge_trade: "Entered soon after a losing exit, with a larger size than that trade.",
  fomo_entry: "Filled beyond the plan's entry, in the chasing direction.",
  averaging_loser: "Added to an open position that was under water.",
  moved_stop_against: "The stop was moved farther from the entry.",
  no_stop: "No protective stop at entry.",
  overtrading: "A trade beyond the day's norm.",
  early_exit: "Closed by hand between the stop and the first target.",
  held_through_event: "Open across the start of a calendar event for this underlying.",
};

export const COMPONENT_LABELS: Record<string, string> = {
  stop_discipline: "Stop discipline",
  plan_adherence: "Plan adherence",
  emotional_control: "Emotional control",
  frequency_control: "Trade frequency",
  event_awareness: "Event awareness",
};

export const SETTING_LABELS: Record<string, { label: string; unit?: string }> = {
  revenge_window_minutes: { label: "Revenge window", unit: "minutes after a losing exit" },
  revenge_size_multiple: { label: "Revenge size multiple", unit: "x the losing trade's size" },
  fomo_chase_r: { label: "FOMO chase", unit: "x the planned risk" },
  overtrading_daily_norm: { label: "Daily trade norm", unit: "trades (0 = your own median)" },
  overtrading_lookback_days: { label: "Own-median look-back", unit: "calendar days" },
  overtrading_multiple: { label: "Own-median multiple", unit: "x" },
  overtrading_min_norm: { label: "Lowest daily norm", unit: "trades" },
};

export const EVENT_KINDS = ["BUDGET", "RBI_POLICY", "EXPIRY", "RESULTS", "FED", "ELECTION", "OTHER"] as const;

export function tagLabel(tag: string): string {
  return TAG_LABELS[tag] ?? tag.replace(/_/g, " ");
}

/** ISO week label ("2026-W41") of a date, using the IST calendar day. */
export function isoWeekOf(date: Date): string {
  const ist = new Date(date.getTime() + 5.5 * 3600 * 1000);
  const day = new Date(Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate()));
  const weekday = day.getUTCDay() || 7;
  day.setUTCDate(day.getUTCDate() + 4 - weekday);                    // the Thursday of this ISO week
  const yearStart = new Date(Date.UTC(day.getUTCFullYear(), 0, 1));
  const week = Math.ceil(((day.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${day.getUTCFullYear()}-W${String(week).padStart(2, "0")}`;
}

/** The week label `offset` weeks from `label` (negative = earlier). */
export function shiftWeek(label: string, offset: number): string {
  const m = /^(\d{4})-W(\d{2})$/.exec(label);
  if (!m) return label;
  const year = Number(m[1]);
  const week = Number(m[2]);
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const monday = new Date(jan4);
  monday.setUTCDate(jan4.getUTCDate() - ((jan4.getUTCDay() || 7) - 1) + (week - 1 + offset) * 7);
  // Noon IST on that Monday, so isoWeekOf reads the same calendar day.
  return isoWeekOf(new Date(monday.getTime() + 6.5 * 3600 * 1000));
}

export function money(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "-";
  const sign = value < 0 ? "-" : "";
  return `${sign}₹${Math.abs(value).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function rText(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? "-" : `${value > 0 ? "+" : ""}${value.toFixed(2)}R`;
}

export function minutesText(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "-";
  if (value < 60) return `${Math.round(value)} min`;
  const hours = Math.floor(value / 60);
  if (hours < 24) return `${hours} h ${Math.round(value - hours * 60)} min`;
  const days = Math.floor(hours / 24);
  return `${days} d ${hours - days * 24} h`;
}

/** Plan vs fill in the trade's own direction: positive = paid more than planned (long) / sold lower (short). */
export function chase(direction: string, planned: number | undefined, fill: number): number | null {
  if (planned == null || !Number.isFinite(planned)) return null;
  const sign = direction === "LONG" ? 1 : -1;
  return Number((sign * (fill - planned)).toFixed(4));
}

/** Tags that count toward the score under the trader's settings. */
export function countsInScore(status: string, countUnconfirmed: boolean): boolean {
  return status === "CONFIRMED" || (countUnconfirmed && status === "SUGGESTED");
}

/** A settings field as typed: blank or not a number is null (a cleared field is never sent as 0). */
export function parseSettingNumber(raw: string): number | null {
  const text = raw.trim();
  if (text === "") return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : null;
}

/** What keeps the settings form from saving: a blank or non-numeric field, or a value outside its range. Empty = ready. */
export function settingsProblems(texts: Record<string, string>, labels: Record<string, string>,
  ranges: Record<string, [number, number]> = {}): string[] {
  const out: string[] = [];
  for (const [key, raw] of Object.entries(texts)) {
    const label = labels[key] ?? key;
    const value = parseSettingNumber(raw);
    const range = ranges[key];
    if (value == null) out.push(`${label}: enter a number${range ? ` from ${range[0]} to ${range[1]}` : ""}`);
    else if (range && (value < range[0] || value > range[1])) out.push(`${label}: ${value} is outside ${range[0]}-${range[1]}`);
  }
  return out;
}
