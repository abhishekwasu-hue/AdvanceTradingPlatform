/**
 * Settings redesign part A (review R6 / A4): one plain sentence per kind of AI failure - the same table as
 * backend/app/ai/byok.py PLAIN_ERRORS. The provider's own error text is never shown.
 */
export type AiErrorKind = "auth" | "no_credit" | "rate" | "model" | "unreachable" | "no_vision" | "other";

export const PLAIN_ERRORS: Readonly<Record<AiErrorKind, string>> = {
  auth: "This key did not work. Check that you copied all of it.",
  no_credit: "This key has no credit left.",
  rate: "Too many requests. Try again in a minute.",
  no_vision: "This provider cannot read charts. Vision needs Gemini, Claude, OpenAI, OpenRouter, Mistral or Cloudflare Workers AI.",
  model: "This provider could not answer right now. Try again later.",
  unreachable: "The AI service could not be reached. Try again in a minute.",
  other: "The AI service did not answer. Try again in a minute.",
};

export function plainError(kind: string | null | undefined): string {
  return PLAIN_ERRORS[(kind ?? "other") as AiErrorKind] ?? PLAIN_ERRORS.other;
}
