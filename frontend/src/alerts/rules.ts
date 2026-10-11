/** S3c (ADR-0022): types and pure helpers for the alert-rules UI - kept out of the components so they are unit-tested.
 * Nothing here sends anything or places an order; a rule only notifies. */

export type RulePriority = "critical" | "normal" | "low";
export type RuleMode = "instant" | "digest";

export interface AlertRule {
  id: number; name: string; kind: "screen" | "instrument"; screen_id: number | null; symbol: string | null;
  condition: string | null; base_tf: string; priority: RulePriority; cooldown_minutes: number; mode: RuleMode;
  digest_every: "hourly" | "eod"; status: "active" | "paused"; expires_at: string | null;
}

export interface AlertRuleInput {
  name: string; kind: "instrument"; symbol: string; condition: string; base_tf: string; priority: RulePriority;
  cooldown_minutes: number; mode: RuleMode; digest_every: "hourly" | "eod";
}

export interface AlertEventRow {
  id: number; symbol: string; bar_time: string; status: "pending" | "sent" | "held" | "suppressed"; reason: string | null;
  group_id: string | null; notification_id: number | null; values: Record<string, unknown>; created_at: string | null;
}

export interface NotificationPolicy {
  timezone: string; quiet_start: string | null; quiet_end: string | null; max_per_hour: number;
  group_window_seconds: number; eod_digest_time: string;
}

export interface DeadLetter {
  id: number; notification_id: number; channel: string; attempts: number; reason: string | null; last_error: string | null;
  group_id: string | null; created_at: string | null;
}

export interface EmailOptOut { id: number; address: string; scope: string; created_at: string }
export interface TelegramLinkCode { code: string; command: string; expires_at: string; how: string }

export const TIMEFRAMES = ["1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w", "1M"] as const;

const REASONS: Record<string, string> = {
  cooldown: "Cooldown - this symbol fired recently for this rule",
  quiet_hours: "Held for quiet hours",
  rate_cap: "Held - hourly cap reached; goes out with the next batch",
  digest: "Waiting for the digest",
  dead_letter: "Gave up after every retry",
  channel_disabled: "Channel was off or removed",
};

/** A plain sentence for a reason code; unknown codes are shown as they are. */
export function reasonLabel(code: string | null | undefined): string {
  if (!code) return "";
  return REASONS[code] ?? code;
}

/** One line describing a rule, e.g. "TCS · close > SMA(close, 20) · 5m · instant · normal". */
export function describeRule(r: AlertRule): string {
  const target = r.kind === "screen" ? `screen #${r.screen_id ?? "?"}` : `${r.symbol ?? "?"} · ${r.condition ?? ""}`;
  const mode = r.mode === "digest" ? `digest (${r.digest_every === "eod" ? "end of day" : "hourly"})` : "instant";
  const cooldown = r.cooldown_minutes > 0 ? ` · cooldown ${r.cooldown_minutes} min` : "";
  return `${target} · ${r.base_tf} · ${mode} · ${r.priority}${cooldown}`;
}

/** The validator's problems from a 422 body (`{"detail": {"message", "problems": [{message, pos}]}}`), as lines with
 * the column when known. Any other body gives []. */
export function problemsFrom(body: string): string[] {
  try {
    const parsed = JSON.parse(body) as { detail?: { problems?: { message?: unknown; pos?: unknown }[] } };
    const problems = parsed.detail?.problems;
    if (!Array.isArray(problems)) return [];
    return problems.filter((p) => typeof p.message === "string").map((p) => (typeof p.pos === "number" ? `${p.message} (at ${p.pos + 1})` : String(p.message)));
  } catch {
    return [];
  }
}

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$/;

/** Client-side checks that mirror the server's (the server stays the authority). Empty = fine. */
export function policyErrors(p: NotificationPolicy): string[] {
  const out: string[] = [];
  if (!p.timezone.trim()) out.push("Timezone is required.");
  if ((p.quiet_start === null) !== (p.quiet_end === null)) out.push("Give both quiet-hours times, or neither.");
  for (const [label, v] of [["Quiet start", p.quiet_start], ["Quiet end", p.quiet_end], ["End-of-day digest", p.eod_digest_time]] as const) {
    if (v !== null && !HHMM.test(v)) out.push(`${label} must be HH:MM (24-hour).`);
  }
  if (!Number.isInteger(p.max_per_hour) || p.max_per_hour < 1 || p.max_per_hour > 600) out.push("Messages per hour must be 1-600.");
  if (!Number.isInteger(p.group_window_seconds) || p.group_window_seconds < 0 || p.group_window_seconds > 600) out.push("Grouping window must be 0-600 seconds.");
  return out;
}

/** Same checks for a new instrument rule. */
export function ruleErrors(r: AlertRuleInput): string[] {
  const out: string[] = [];
  if (!r.name.trim()) out.push("Name is required.");
  if (!/^[A-Za-z0-9 &._:-]{1,40}$/.test(r.symbol.trim())) out.push("Symbol looks wrong.");
  if (!r.condition.trim()) out.push("Condition is required (ScreenQL, e.g. close > SMA(close, 20)).");
  if (!(TIMEFRAMES as readonly string[]).includes(r.base_tf)) out.push("Pick a timeframe.");
  if (!Number.isInteger(r.cooldown_minutes) || r.cooldown_minutes < 0 || r.cooldown_minutes > 10080) out.push("Cooldown must be 0-10080 minutes.");
  return out;
}

/** "" -> null for the optional HH:MM fields. */
export function blankToNull(v: string): string | null {
  return v.trim() === "" ? null : v.trim();
}
