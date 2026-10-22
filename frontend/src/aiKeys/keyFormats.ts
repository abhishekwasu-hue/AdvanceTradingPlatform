import formats from "./__fixtures__/key_formats.json";

/**
 * Settings redesign part A (review R5): which provider a pasted key belongs to, from its format alone. The rows come from
 * the shared table (`__fixtures__/key_formats.json`, a byte-identical copy of backend/tests/fixtures/key_formats.json,
 * the same table as backend/app/ai/key_formats.py), so the two sides cannot drift; keyFormats.test.ts runs every case.
 * First match wins: sign-in sessions, OAuth tokens and admin keys are refused first, OpenRouter (`sk-or-`) and Groq
 * (`gsk_`) are their own providers (never OpenAI), xAI keys are not taken, then Claude, OpenAI and Google Gemini.
 * Mistral and Z.ai keys have no prefix: they come out "unknown" and the picker asks. The server checks again and is the
 * authority. Nothing here keeps or logs a key.
 *
 * Master Order 2 part A: `PROVIDER_FACTS` (the same table as backend/app/ai/key_formats.py) says what each provider is:
 * its plain name, the "Free tier" and "Reads charts (vision)" tags, whether its free tier may train on what it is sent,
 * and its one-line data term.
 */

/** Every provider "Connect AI in one paste" takes (behind `ai_one_key`). The Vision card keeps ByokProviderName's three. */
export type AiProviderName = "gemini" | "anthropic" | "openai" | "openrouter" | "groq" | "mistral" | "zai";

export interface ProviderFacts { name: string; free_tier: boolean; vision: boolean; may_train: boolean; privacy: string }

export type KeyKind = "provider" | "unsupported" | "session" | "admin" | "unknown" | "empty";

export interface KeyFormatRow { prefix: string; kind: KeyKind; provider: AiProviderName | null; name: string }

export interface Detection { kind: KeyKind; provider: AiProviderName | null; name: string }

export const KEY_FORMATS: readonly KeyFormatRow[] = formats.table as KeyFormatRow[];
const SESSION_MARKERS: readonly string[] = formats.session_markers;
export const PROVIDER_FACTS: Readonly<Record<AiProviderName, ProviderFacts>> = formats.provider_facts as Record<AiProviderName, ProviderFacts>;
/** A provider whose free tier may use what it is sent for training. */
export const MAY_TRAIN_ON_FREE_TIER: Readonly<Record<AiProviderName, boolean>> = formats.may_train_on_free_tier as Record<AiProviderName, boolean>;

/** The plain names the panel shows. */
export const PROVIDER_LABELS: Readonly<Record<AiProviderName, string>> = Object.fromEntries(
  Object.entries(PROVIDER_FACTS).map(([id, f]) => [id, f.name])) as Record<AiProviderName, string>;
/** Free Google Gemini first, then the others (the guides' order). */
export const CONNECT_ORDER: readonly AiProviderName[] = formats.connect_order as AiProviderName[];
/** The small picker for a key whose format does not say (Mistral and Z.ai keys have no prefix). */
export const PICKER_ORDER: readonly AiProviderName[] = formats.picker_order as AiProviderName[];
/** The services that read charts, in plain words (the no-vision sentence names them). */
export const VISION_NAMES: readonly string[] = CONNECT_ORDER.filter((p) => PROVIDER_FACTS[p].vision).map((p) => PROVIDER_LABELS[p]);

/** The key as pasted, without the spaces, line breaks and quotes a copy often brings along. */
export function cleanKey(raw: string | null | undefined): string {
  return (raw ?? "").trim().replace(/^["'`]+|["'`]+$/g, "").trim();
}

/** A cookie header, a JWT, an OAuth token or a sign-in session - never an API key. */
export function looksLikeSession(key: string): boolean {
  const lower = key.toLowerCase();
  if (KEY_FORMATS.some((r) => r.kind === "session" && lower.startsWith(r.prefix))) return true;
  if (SESSION_MARKERS.some((m) => lower.includes(m))) return true;
  return key.includes("=") || key.includes(";") || key.split(".").length - 1 >= 2;
}

export function detectProvider(raw: string | null | undefined): Detection {
  const key = cleanKey(raw);
  if (!key) return { kind: "empty", provider: null, name: "" };
  if (looksLikeSession(key)) {
    const row = KEY_FORMATS.find((r) => r.kind === "session" && key.toLowerCase().startsWith(r.prefix));
    return { kind: "session", provider: null, name: row?.name ?? "sign-in token" };
  }
  for (const row of KEY_FORMATS) {
    if (row.kind === "session") continue;
    if (key.startsWith(row.prefix) || (row.kind === "admin" && key.toLowerCase().startsWith(row.prefix))) {
      return { kind: row.kind, provider: row.provider, name: row.name };
    }
  }
  return { kind: "unknown", provider: null, name: "" };
}

/** The one line the panel shows for a key it cannot take, or null. */
export function refusalFor(found: Detection): string | null {
  if (found.kind === "session") return "This looks like a sign-in token, not an API key. Create an API key on the provider's key page and paste that.";
  if (found.kind === "admin") return "This is an organisation admin key. Use a standard API key from the provider's key page.";
  if (found.kind === "unsupported") {
    const article = /^[AEIOU]/i.test(found.name) ? "an" : "a";
    return `This looks like ${article} ${found.name} key. AiAlgoDesk cannot use ${found.name} keys. ${ONE_KEY_PROVIDERS_LINE}`;
  }
  return null;
}

/** The same line as the server's (app/ai/byok.py ONE_KEY_PROVIDERS_LINE). */
export const ONE_KEY_PROVIDERS_LINE = "Use a key from Google Gemini, Claude, OpenAI, OpenRouter, Groq, Mistral or Z.ai.";
