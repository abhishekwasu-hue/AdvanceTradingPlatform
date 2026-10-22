import type { ByokProviderName } from "../types";

/**
 * MO2 part B: the beginner guides to getting an AI key, one per provider ATP takes today (Settings → AI). Google Gemini
 * first and recommended: it has a free tier. More providers come with MO2 part A.
 *
 * - `key_page`: the provider's own key page, opened in a new tab. `checked_on`: the day someone last opened that page
 *   and checked the steps against it; a test (src/aiKeys/aiKeys.test.tsx) fails 120 days later, so it is checked again.
 * - Screenshots: each step may name the picture it needs; until a real one is taken (by us, of our own account) the guide
 *   shows a marked placeholder. No third-party picture is fetched or committed.
 * - What a key looks like is its prefix and length only, never a real key.
 * - Plain words, no prices promised: what is free and what is paid is the provider's to change, so the guide says where
 *   to check.
 */

export interface GuideStep {
  text: string;
  /** What the screenshot for this step will show (a marked placeholder until it exists). */
  screenshot?: string;
}

export interface GuideError {
  /** What the trader sees (the provider's words, shortened). */
  problem: string;
  fix: string;
  /** Matched against a failed "Test key" message to point at this fix. */
  match: RegExp;
}

export interface KeyGuide {
  provider: ByokProviderName;
  name: string;
  /** The tag next to the name: "Free tier" or "Paid". */
  cost: "Free tier" | "Paid";
  recommended: boolean;
  /** The "Open <provider> key page" button. */
  key_page: { label: string; url: string };
  /** YYYY-MM-DD: when the page and the steps were last checked. */
  checked_on: string;
  steps: readonly GuideStep[];
  /** What the key looks like: its prefix and its rough length, never a real key. */
  looks_like: { prefix: string; text: string };
  is_it_free: string;
  errors: readonly GuideError[];
}

export const GUIDE_MAX_AGE_DAYS = 120;

const PASTE_STEP: GuideStep = { text: "Come back to this page, paste the key in the box below and press Save and test." };

export const KEY_GUIDES: readonly KeyGuide[] = [
  {
    provider: "gemini",
    name: "Google Gemini",
    cost: "Free tier",
    recommended: true,
    key_page: { label: "Open Google AI Studio key page", url: "https://aistudio.google.com/apikey" },
    checked_on: "2026-10-22",
    steps: [
      { text: "Press Open Google AI Studio key page and sign in with your Google account.", screenshot: "The Google AI Studio sign-in page" },
      { text: "If Google asks, accept its terms for AI Studio." },
      { text: "Press Create API key. If it asks for a project, pick the one it suggests or let it create one.", screenshot: "The Create API key button in AI Studio" },
      { text: "Your new key appears in a box. Press the copy button next to it.", screenshot: "A new key with its copy button (key hidden)" },
      PASTE_STEP,
    ],
    looks_like: { prefix: "AIza", text: "Starts with AIza and is about 39 letters and numbers long, with no spaces." },
    is_it_free: "Yes, to start. Google AI Studio has a free tier with daily limits and no card is needed. On the free tier Google may use what you send to improve its products; turning on billing in Google Cloud lifts the limits and changes that. Google's pricing page has the current limits.",
    errors: [
      { problem: "API key not valid", fix: "Copy the key again with the copy button, so no letter is missed, and paste it here.", match: /not valid|invalid|api key/i },
      { problem: "Quota exceeded or too many requests", fix: "The free daily limit is used up. Try again tomorrow, or turn on billing for your Google Cloud project.", match: /quota|429|exhausted|rate/i },
      { problem: "Not available in your country", fix: "Google AI Studio is not offered everywhere. Use another provider below.", match: /location|region|country|not supported/i },
    ],
  },
  {
    provider: "anthropic",
    name: "Anthropic (Claude)",
    cost: "Paid",
    recommended: false,
    key_page: { label: "Open Anthropic key page", url: "https://console.anthropic.com/settings/keys" },
    checked_on: "2026-10-22",
    steps: [
      { text: "Press Open Anthropic key page and sign in to the Anthropic Console, or create an account.", screenshot: "The Anthropic Console sign-in page" },
      { text: "Open Billing and add credit. A key does not work until the account has credit.", screenshot: "The Billing page of the Anthropic Console" },
      { text: "Go back to API keys and press Create Key. Name it AiAlgoDesk so you know where it is used." },
      { text: "Copy the key now: the Console shows it only once.", screenshot: "A new key with its copy button (key hidden)" },
      PASTE_STEP,
    ],
    looks_like: { prefix: "sk-ant-", text: "Starts with sk-ant- and is about 100 characters long." },
    is_it_free: "No. You pay Anthropic for what you use, from credit you add in the Console. A Claude Pro or Max chat subscription does not include API use and cannot be used here.",
    errors: [
      { problem: "Invalid API key or authentication error", fix: "Copy the whole key again (it starts with sk-ant-) and paste it here.", match: /invalid|authentication|x-api-key|401/i },
      { problem: "Your credit balance is too low", fix: "Add credit under Billing in the Anthropic Console, then press Save and test again.", match: /credit|balance|billing/i },
      { problem: "Rate limit reached", fix: "Wait a minute and try again.", match: /rate|429|overloaded/i },
    ],
  },
  {
    provider: "openai",
    name: "OpenAI",
    cost: "Paid",
    recommended: false,
    key_page: { label: "Open OpenAI key page", url: "https://platform.openai.com/api-keys" },
    checked_on: "2026-10-22",
    steps: [
      { text: "Press Open OpenAI key page and sign in to the OpenAI platform, or create an account.", screenshot: "The OpenAI platform sign-in page" },
      { text: "Under Settings, open Billing and add a payment method or credit. A key does not work without it." },
      { text: "Go back to API keys and press Create new secret key. Name it AiAlgoDesk.", screenshot: "The Create new secret key button" },
      { text: "Copy the key now: OpenAI shows it only once.", screenshot: "A new key with its copy button (key hidden)" },
      PASTE_STEP,
    ],
    looks_like: { prefix: "sk-", text: "Starts with sk- (new keys with sk-proj-) and is long, often more than 100 characters." },
    is_it_free: "No. You pay OpenAI for what you use, from a payment method or credit under Billing. A ChatGPT Plus subscription does not include API use and cannot be used here.",
    errors: [
      { problem: "Incorrect API key provided", fix: "Copy the whole key again (it starts with sk-) and paste it here.", match: /incorrect|invalid|401|api key/i },
      { problem: "You exceeded your current quota", fix: "Add a payment method or credit under Billing on the OpenAI platform, then press Save and test again.", match: /quota|billing|insufficient/i },
      { problem: "Rate limit reached", fix: "Wait a minute and try again.", match: /rate|429/i },
    ],
  },
];

export function guideFor(provider: ByokProviderName): KeyGuide {
  return KEY_GUIDES.find((g) => g.provider === provider) ?? KEY_GUIDES[0];
}

/** The guide's fix for a failed test's message, if one of its common errors matches. */
export function fixFor(guide: KeyGuide, message: string | null | undefined): GuideError | null {
  if (!message) return null;
  return guide.errors.find((e) => e.match.test(message)) ?? null;
}

/** Days from `checked_on` to `today` (both YYYY-MM-DD or Dates). */
export function guideAgeDays(checkedOn: string, today: Date = new Date()): number {
  const then = Date.parse(`${checkedOn}T00:00:00Z`);
  const now = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  return Math.floor((now - then) / 86_400_000);
}
