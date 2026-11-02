/**
 * M76 MP-7 (owner order M76 S-3; flag `copilot_workspace`): event awareness on the workspace's chart - the pure rules and
 * the reads. The server (`GET /api/charts/workspace/events`, backend app/charts/workspace_events.py) gives, for the
 * chart's underlying from today up to its next monthly expiry: the listed expiries (weekly / monthly from the instrument
 * master's own list), the events calendar's entries and his own event list (prefs `own_events`). The check card's event
 * line reads his own events too (the ticket's `/paper/fno/event-risk`, marked "own").
 *
 * Information only: nothing here places, blocks or changes an order or an exit; no event is chosen for him - his list is
 * empty until he adds one, and every row is a date and a name (no advice, no judgement).
 */
import { request } from "../../api/client";
import { formatDateIST } from "../../ui/format";
import type { RiskEvent } from "../../fno/riskParts";

export interface ExpiryRow { date: string; kind: "weekly" | "monthly" | "expiry"; name: string }
export interface WorkspaceEvents {
  underlying: string; from: string; until: string; expiries: ExpiryRow[]; events: RiskEvent[]; more: number;
  calendar_empty: boolean; message: string | null; source: string; blocks: false;
}
/** One entry of his own list, as saved (prefs `own_events`). */
export interface OwnEvent { date: string; name: string; underlying: string | null }

/** At most this many in his list (the server's MAX_OWN_EVENTS). */
export const MAX_OWN_EVENTS = 30;
/** His own words: the prefs' name rule (letters, digits and simple marks, up to 40). */
export const OWN_NAME = /^[A-Za-z0-9][A-Za-z0-9 %()+\-/.,:<>=]{0,39}$/;

interface PrefsRead { prefs?: Record<string, unknown>; versions?: Record<string, number> }

/** His saved list as the page reads it (anything malformed dropped). Pure. */
export function readOwnEvents(raw: unknown): OwnEvent[] {
  const list = (raw as { events?: unknown } | null | undefined)?.events;
  if (!Array.isArray(list)) return [];
  return list.flatMap((e) => {
    const x = e as Partial<OwnEvent> | null;
    if (!x || typeof x.date !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(x.date) || typeof x.name !== "string") return [];
    return [{ date: x.date, name: x.name, underlying: typeof x.underlying === "string" && x.underlying ? x.underlying : null }];
  });
}

export const eventsApi = {
  read: (symbol: string) => request<WorkspaceEvents>(`/charts/workspace/events?${new URLSearchParams({ symbol }).toString()}`),
  loadOwn: async () => {
    const r = await request<PrefsRead>("/me/prefs");
    return { list: readOwnEvents(r.prefs?.own_events), version: r.versions?.own_events ?? 0 };
  },
  saveOwn: async (events: OwnEvent[], version: number) => {
    const r = await request<{ value: unknown; version: number }>(`/me/prefs/own_events?version=${version}`, { method: "PUT", body: JSON.stringify({ events }) });
    return { list: readOwnEvents(r.value), version: r.version };
  },
};
export type EventsApi = typeof eventsApi;

/** One chip of the strip under the chart. */
export interface EventChip { key: string; date: string; text: string; tone: "expiry" | "market" | "company" | "own" }

/** The strip's chips, by date (an expiry first on its day): "Thu 06 Nov · Weekly expiry", "Wed 05 Nov · RBI policy". Pure. */
export function eventChips(v: WorkspaceEvents | null): EventChip[] {
  if (!v) return [];
  const day = (d: string) => formatDateIST(d, { weekday: true });
  const rows: (EventChip & { order: number })[] = [
    ...v.expiries.map((e) => ({ key: `x-${e.date}`, date: e.date, text: `${day(e.date)} · ${e.name}`, tone: "expiry" as const, order: 0 })),
    ...v.events.map((e, i) => ({
      key: `e-${i}-${e.date}`, date: e.date, order: 1,
      text: `${day(e.date)} · ${e.name}${e.source === "own" ? " (yours)" : ""}${e.scope && e.scope !== "all symbols" && e.scope !== v.underlying ? ` · ${e.scope}` : ""}`,
      tone: (e.source === "own" ? "own" : e.source === "company" ? "company" : "market") as EventChip["tone"],
    })),
  ];
  return rows.sort((a, b) => (a.date === b.date ? a.order - b.order : a.date < b.date ? -1 : 1))
    .map((c) => ({ key: c.key, date: c.date, text: c.text, tone: c.tone }));
}

/** The line under the strip when there is nothing to show, or the calendar is empty. Pure. */
export function eventsNote(v: WorkspaceEvents | null): string | null {
  if (!v) return null;
  if (v.calendar_empty && !v.expiries.length) return v.message ?? "No events or expiries are known for this chart.";
  if (!v.expiries.length && !v.events.length) return `No event in your events calendar up to ${formatDateIST(v.until)}.`;
  return v.more > 0 ? `And ${v.more} more up to ${formatDateIST(v.until)}.` : null;
}

/** A new entry from what he typed, or the first thing still missing. Pure. */
export function ownEventOf(date: string, name: string, forSymbol: string | null): OwnEvent | string {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return "Pick the date.";
  const n = name.trim().replace(/\s+/g, " ");
  if (!n) return "Type what the event is.";
  if (!OWN_NAME.test(n)) return "Use letters, digits and simple marks only (up to 40).";
  return { date, name: n, underlying: forSymbol ? forSymbol.trim().toUpperCase() : null };
}

/** His list with one more entry, in date order (at most MAX_OWN_EVENTS). Pure. */
export function addOwn(list: readonly OwnEvent[], e: OwnEvent): OwnEvent[] | null {
  if (list.length >= MAX_OWN_EVENTS) return null;
  return [...list, e].sort((a, b) => (a.date < b.date ? -1 : a.date > b.date ? 1 : 0));
}
