import type { AiProviderName } from "./keyFormats";

/**
 * Settings redesign part A: the Connect AI panel's server state (GET /api/ai/byok/connection, behind `ai_one_key`).
 * The key itself never comes back: only its last four characters.
 */
export interface AiConnection {
  connected: boolean;
  provider: AiProviderName | null;
  /** The plain name: Claude / OpenAI / Google Gemini / OpenRouter / Groq / Mistral / Z.ai GLM. */
  label: string | null;
  key_end: string | null;
  /** The last test: true, false, or null (not tested yet). */
  ok: boolean | null;
  /** One plain sentence when the last test failed. */
  error: string | null;
  tested_at: string | null;
  consent: { accepted: boolean; text: string } | null;
  may_train: boolean;
  paid_plan: boolean;
  paid_plan_text?: string | null;
  free_tier_warning: string | null;
  /** MO2 part A: whether the vision features (chart analysis) can use this key; null when not connected. */
  vision?: boolean | null;
  /** MO2 part A: the provider's one-line data term. */
  privacy?: string | null;
  limits: AiLimits;
  /** "Use this key for your account too" - only for the owner who entered the organisation's key. */
  copy_tenant_key: { label: string } | null;
}

export interface AiLimits {
  month_inr: number;
  monthly_cap_inr: number;
  today_inr: number;
  daily_cap_inr: number;
  max_cap_inr: number;
  /** "AI this month: ₹X of your ₹Y limit". */
  line: string;
  reached: "day" | "month" | null;
}

/** Fired with an AI call that answered `ai_not_connected`: the shell opens the Connect AI panel. */
export const CONNECT_AI_EVENT = "atp:connect-ai";
/** Fired after the panel connected, changed or removed the key, so pages that show the AI state read it again. */
export const AI_CONNECTION_CHANGED_EVENT = "atp:ai-connection-changed";
export const NOT_CONNECTED_CODE = "ai_not_connected";

/** True for an error body that carries the one refusal code. */
export function isNotConnected(body: string | null | undefined): boolean {
  return !!body && body.includes(`"${NOT_CONNECTED_CODE}"`);
}
