import { keyShapeError } from "../components/visionByok";
import type { ByokProviderName } from "../types";

/**
 * MO2 part B: the paste helper's rules. The server checks the key again (app/ai/byok.py validate_key_shape) and is the
 * authority; this only tells the trader early, in plain words, what was pasted. Nothing here keeps or logs the key, and
 * no message repeats it.
 */

/** Tells the "Vision with your own AI key" card to read its status again after the guide's helper saved a key. */
export const BYOK_CHANGED_EVENT = "atp:byok-changed";

export const PROVIDER_NAMES: Record<ByokProviderName, string> = { gemini: "Google Gemini", anthropic: "Anthropic (Claude)", openai: "OpenAI" };

/** Keys of services ATP does not take yet, recognised so the message can say so instead of "wrong key". */
const OTHER_SERVICES: readonly { prefix: string; name: string }[] = [
  { prefix: "sk-or-", name: "OpenRouter" },
  { prefix: "gsk_", name: "Groq" },
  { prefix: "xai-", name: "xAI" },
];

/** The key as pasted, without the spaces, line breaks and quotes a copy often brings along. */
export function cleanPastedKey(raw: string): string {
  return raw.trim().replace(/^["'`]+|["'`]+$/g, "").trim();
}

export type Detected = { kind: "provider"; provider: ByokProviderName } | { kind: "other"; name: string } | { kind: "unknown" };

/** Which service a key belongs to, from its prefix. */
export function detectKey(key: string): Detected {
  const k = key.trim();
  for (const o of OTHER_SERVICES) if (k.startsWith(o.prefix)) return { kind: "other", name: o.name };
  if (k.startsWith("sk-ant-")) return { kind: "provider", provider: "anthropic" };
  if (k.startsWith("AIza")) return { kind: "provider", provider: "gemini" };
  if (k.startsWith("sk-")) return { kind: "provider", provider: "openai" };
  return { kind: "unknown" };
}

export interface PasteCheck {
  /** The cleaned key (what is sent on Save). */
  key: string;
  detected: Detected;
  /** A wrong provider's key: switching the guide fixes it. */
  switchTo: ByokProviderName | null;
  /** The one message to show, or null when the key looks right. */
  warning: string | null;
  /** True when Save and test may be pressed. */
  ok: boolean;
}

const withArticle = (name: string) => `${/^[AEIOU]/i.test(name) ? "an" : "a"} ${name}`;

/** What the paste helper says about `raw` for the guide of `provider`. */
export function checkPastedKey(provider: ByokProviderName, raw: string): PasteCheck {
  const key = cleanPastedKey(raw);
  const detected = detectKey(key);
  if (!key) return { key, detected, switchTo: null, warning: null, ok: false };
  if (detected.kind === "other") {
    return { key, detected, switchTo: null, ok: false, warning: `This looks like ${withArticle(detected.name)} key. ATP cannot use ${detected.name} keys yet; pick one of the providers here.` };
  }
  if (detected.kind === "provider" && detected.provider !== provider) {
    return { key, detected, switchTo: detected.provider, ok: false,
      warning: `This looks like ${withArticle(PROVIDER_NAMES[detected.provider])} key, not ${withArticle(PROVIDER_NAMES[provider])} key.` };
  }
  const shape = keyShapeError(provider, key);
  if (shape) return { key, detected, switchTo: null, ok: false, warning: shape };
  return { key, detected, switchTo: null, ok: true, warning: null };
}
