import type { AiAction } from "../types";

/**
 * PW5 snooze helpers (pure). A snooze hides a proposal from the pending list until it wakes; the server cuts it at the
 * proposal's expiry (a snooze never extends it), and a snoozed proposal never holds up an exit.
 */
const IST_OFFSET_MS = 330 * 60 * 1000;          // India has no daylight saving: always UTC+05:30

export type SnoozeKey = "15m" | "1h" | "0900";

export interface SnoozeChoice {
  key: SnoozeKey;
  body: { minutes: number } | { until: string };
  wakesAt: Date;
  /** The proposal expires before this snooze would end: it is snoozed until it expires instead. */
  cutAtExpiry: boolean;
  /** For "0900": whether 09:00 IST is today or tomorrow. */
  tomorrow?: boolean;
}

/** The next 09:00 IST after `now` (today's if it is still before 09:00 IST, else tomorrow's). */
export function next0900Ist(now: Date): { at: Date; tomorrow: boolean } {
  const ist = new Date(now.getTime() + IST_OFFSET_MS);
  const today = Date.UTC(ist.getUTCFullYear(), ist.getUTCMonth(), ist.getUTCDate(), 9, 0, 0) - IST_OFFSET_MS;
  return today > now.getTime() ? { at: new Date(today), tomorrow: false } : { at: new Date(today + 24 * 3600 * 1000), tomorrow: true };
}

export function snoozeChoices(now: Date, expiresAt: string | null | undefined): SnoozeChoice[] {
  const expires = expiresAt ? new Date(expiresAt).getTime() : Number.POSITIVE_INFINITY;
  const morning = next0900Ist(now);
  const choices: SnoozeChoice[] = [
    { key: "15m", body: { minutes: 15 }, wakesAt: new Date(now.getTime() + 15 * 60 * 1000), cutAtExpiry: false },
    { key: "1h", body: { minutes: 60 }, wakesAt: new Date(now.getTime() + 60 * 60 * 1000), cutAtExpiry: false },
    { key: "0900", body: { until: morning.at.toISOString() }, wakesAt: morning.at, cutAtExpiry: false, tomorrow: morning.tomorrow },
  ];
  return choices.map((c) => (c.wakesAt.getTime() > expires ? { ...c, wakesAt: new Date(expires), cutAtExpiry: true } : c));
}

/** True while the proposal is snoozed (the server hides it from the pending list for exactly this long). */
export function isSnoozed(a: Pick<AiAction, "status" | "snoozed_until">, now: Date): boolean {
  return a.status === "PROPOSED" && !!a.snoozed_until && new Date(a.snoozed_until).getTime() > now.getTime();
}

/** Time left before the proposal expires, rounded down to minutes; null once it has expired (or has no expiry). */
export function timeLeft(expiresAt: string | null | undefined, now: Date): { hours: number; minutes: number } | null {
  if (!expiresAt) return null;
  const ms = new Date(expiresAt).getTime() - now.getTime();
  if (!(ms > 0)) return null;
  const total = Math.floor(ms / 60000);
  return { hours: Math.floor(total / 60), minutes: total % 60 };
}

/** A short evidence summary: up to `max` "key: value" pairs of the scalar evidence, numbers rounded. */
export function evidenceSummary(evidence: Record<string, unknown> | null | undefined, max = 3): string[] {
  const out: string[] = [];
  for (const [key, value] of Object.entries(evidence ?? {})) {
    if (out.length >= max) break;
    const label = key.replace(/_/g, " ");
    if (typeof value === "number") out.push(`${label}: ${Number.isInteger(value) ? value : value.toFixed(2)}`);
    else if (typeof value === "string" && value.length <= 60) out.push(`${label}: ${value}`);
    else if (Array.isArray(value) && value.length <= 6 && value.every((v) => typeof v === "number")) out.push(`${label}: ${value.join(", ")}`);
  }
  return out;
}
