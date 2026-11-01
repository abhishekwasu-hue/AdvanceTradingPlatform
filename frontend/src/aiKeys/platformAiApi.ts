import { request } from "../api/client";

/**
 * M72 PR-1b (M72Q-1): the platform AI key (backend app/ai/platform_key.py, flag `ai_platform_key`). The key itself never
 * reaches the browser: the admin sees only whether the server has one; a user sees only his own use today.
 */

/** Settings › AI: the caller's own use today (404 while the flag is off). */
export interface PlatformUsage {
  in_use: boolean;            // the platform key is what answers him (no key of his own or of his organisation)
  available: boolean;         // it answers right now (limits set, none reached)
  reason: string | null;
  message: string | null;
  today_inr: number;
  cap_inr: number | null;     // the limit that applies (the lower of the operator's and his own)
  operator_cap_inr: number | null;
  own_cap_inr: number | null;
}

/** Admin › Controls › Limits. Every number is null = Not set (ATP chooses none). */
export interface PlatformLimits {
  plan_budget_inr: Record<string, number | null>;
  user_daily_cap_inr: number | null;
  key_configured: boolean;
  provider: string | null;
  plans: { id: string; name: string }[];
}

export const platformAiApi = {
  usage: () => request<PlatformUsage>("/ai/platform/usage"),
  /** His own lower daily limit (`user_prefs` namespace `ai_usage`); null clears it. */
  setOwnCap: (inr: number | null) =>
    request<{ value: { daily_cap_inr: number | null } }>("/me/prefs/ai_usage", { method: "PUT", body: JSON.stringify({ daily_cap_inr: inr }) }),
  limits: () => request<PlatformLimits>("/admin/controls/ai-platform"),
  setLimits: (body: { plan_budget_inr?: Record<string, number | null>; user_daily_cap_inr?: number | null; clear_cap?: boolean }) =>
    request<PlatformLimits>("/admin/controls/ai-platform", { method: "PUT", body: JSON.stringify(body) }),
};

/** A typed rupee amount: "" = Not set (null); anything else must be a number of 0 or more (else undefined = invalid). */
export function parseRupees(text: string): number | null | undefined {
  const t = text.trim().replace(/[₹,\s]/g, "");
  if (t === "") return null;
  const n = Number(t);
  return Number.isFinite(n) && n >= 0 ? n : undefined;
}

/** How much of today's limit is used, 0..100 (null when no limit applies). */
export function usedPct(today: number, cap: number | null): number | null {
  if (cap === null || cap <= 0) return null;
  return Math.max(0, Math.min(100, (today / cap) * 100));
}
