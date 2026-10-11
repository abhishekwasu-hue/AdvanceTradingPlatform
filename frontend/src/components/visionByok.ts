// BYOK-V1: pure helpers for the "Vision with your own AI key" card. The server validates the key again (and is the
// authority); this only gives the user an early, specific message. Messages never contain the key itself.
import type { ByokProviderName } from "../types";

export const BYOK_PROVIDERS: ByokProviderName[] = ["anthropic", "openai", "gemini"];

export const MIN_KEY_LENGTH = 20;
export const MAX_KEY_LENGTH = 256;

export const SESSION_TOKEN_MESSAGE =
  "This looks like a sign-in session, cookie or OAuth token, not an API key. Subscription sign-in is not supported - create an API key in the provider's developer console and paste that.";

// Claude.ai session keys and OAuth tokens (subscription sign-in), Google OAuth tokens, browser session tokens, JWTs.
const SESSION_PREFIXES = ["sk-ant-sid", "sk-ant-oat", "sk-ant-ort", "ya29.", "1//", "sess-", "eyj", "bearer "];
const SESSION_MARKERS = ["session", "cookie", "oauth", "refresh_token", "access_token"];

export function looksLikeSessionToken(key: string): boolean {
  const lower = key.toLowerCase();
  if (SESSION_PREFIXES.some((p) => lower.startsWith(p)) || SESSION_MARKERS.some((m) => lower.includes(m))) return true;
  return key.includes("=") || key.includes(";") || key.split(".").length > 2;
}

/** null when the key has the right shape for the provider, else a message for the user. */
export function keyShapeError(provider: ByokProviderName, raw: string): string | null {
  const key = raw.trim();
  if (!key) return "Paste an API key";
  if (looksLikeSessionToken(key)) return SESSION_TOKEN_MESSAGE;
  if (key.length < MIN_KEY_LENGTH || key.length > MAX_KEY_LENGTH) return `An API key is ${MIN_KEY_LENGTH} to ${MAX_KEY_LENGTH} characters long`;
  if (!/^[A-Za-z0-9_-]+$/.test(key)) return "An API key contains only letters, digits, '-' and '_' - paste the key alone, without quotes or spaces";
  const lower = key.toLowerCase();
  if (lower.startsWith("sk-ant-admin") || lower.startsWith("sk-admin-")) return "This is an organisation admin key. Use a standard API key from the provider's console";
  if (provider === "anthropic" && !key.startsWith("sk-ant-")) return "An Anthropic API key starts with 'sk-ant-'";
  if (provider === "openai" && (!key.startsWith("sk-") || key.startsWith("sk-ant-"))) return "An OpenAI API key starts with 'sk-'";
  if (provider === "gemini" && key.startsWith("sk-")) return "This looks like an Anthropic or OpenAI key, not a Google Gemini key";
  return null;
}

/** USD with enough precision for tiny test-call costs ($0.0004) and plain cents for larger amounts. */
export function formatUsd(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "$0.00";
  if (value < 0.01) return `$${value.toFixed(4)}`;
  return `$${value.toFixed(2)}`;
}

export interface CapUsage { pct: number; reached: boolean; label: string }

/** Spend against a cap: percent for the bar (0-100), whether the hard stop applies, and a short label. */
export function capUsage(spent: number, cap: number): CapUsage {
  const safeSpent = Number.isFinite(spent) && spent > 0 ? spent : 0;
  if (!Number.isFinite(cap) || cap <= 0) return { pct: 100, reached: true, label: `${formatUsd(safeSpent)} of $0.00 - cap is zero, Vision is stopped` };
  const reached = safeSpent >= cap;
  const pct = Math.min(100, Math.round((safeSpent / cap) * 100));
  return { pct, reached, label: `${formatUsd(safeSpent)} of ${formatUsd(cap)}${reached ? " - cap reached, Vision is stopped" : ""}` };
}

/** A cap typed in the form: a number between 0 and the maximum, or a message. */
export function parseCap(raw: string, max: number): { value: number | null; error: string | null } {
  const text = raw.trim();
  if (!text) return { value: null, error: "Enter a cap in USD" };
  const value = Number(text);
  if (!Number.isFinite(value) || value < 0) return { value: null, error: "A cap is a number of USD, 0 or more" };
  if (value > max) return { value: null, error: `A cap can be at most $${max}` };
  return { value: Math.round(value * 100) / 100, error: null };
}
