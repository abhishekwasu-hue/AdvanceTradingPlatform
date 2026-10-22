import { PROVIDER_FACTS, type AiProviderName } from "./keyFormats";

/**
 * MO2 part B: the beginner guides to getting an AI key, one per provider ATP takes (Settings → AI). Google Gemini first
 * and recommended: it has a free tier and reads charts.
 * MO2 part A: OpenRouter, Groq, Mistral and Z.ai GLM too - shown only where the Connect AI panel takes them (behind
 * `ai_one_key`); the Vision card's guides (without it) stay the three it takes (`VISION_CARD_GUIDES`). Each guide's
 * tags ("Free tier" / "Paid", "Reads charts (vision)", "May train on free keys") and its data line come from the one
 * provider table (`PROVIDER_FACTS`, the same as the server's).
 * The four new guides were checked on 2026-10-22 against the provider's own docs. The provider websites could not be
 * opened directly from the build machine that day, so the steps and addresses were read from the provider's docs
 * sources and a search limited to its docs domain: openrouter.ai/docs (quickstart, install, free models),
 * console.groq.com/docs (quickstart, your-data), Mistral's docs source github.com/mistralai/platform-docs-public
 * (activate-and-generate-api-key) and docs.z.ai (quick-start, the API keys page link). Re-check them in the browser at
 * the next review.
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
  provider: AiProviderName;
  name: string;
  /** The tag next to the name: "Free tier", "Free credits (run out)" or "Paid". */
  cost: "Free tier" | "Free credits (run out)" | "Paid";
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

/** The tags and the data line of a guide's provider (the shared provider table). */
export function guideTags(guide: KeyGuide): { vision: boolean; may_train: boolean; privacy: string } {
  const facts = PROVIDER_FACTS[guide.provider];
  return { vision: facts.vision, may_train: facts.may_train, privacy: facts.privacy };
}

/** The privacy notice shown before a free-tier key is saved (MO2 part A, the order's words). */
export const FREE_TIER_NOTICE = "Free tiers of some providers may use your prompts, charts and data to improve or train their models. Use a paid tier for private strategies.";

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
  {
    provider: "openrouter",
    name: "OpenRouter",
    cost: "Free tier",
    recommended: false,
    key_page: { label: "Open OpenRouter key page", url: "https://openrouter.ai/settings/keys" },
    checked_on: "2026-10-22",
    steps: [
      { text: "Press Open OpenRouter key page and sign in, or create an account.", screenshot: "The OpenRouter sign-in page" },
      { text: "Press Create API Key. Name it AiAlgoDesk so you know where it is used.", screenshot: "The Create API Key button on OpenRouter" },
      { text: "Copy the key now: OpenRouter shows it only once.", screenshot: "A new key with its copy button (key hidden)" },
      PASTE_STEP,
    ],
    looks_like: { prefix: "sk-or-", text: "Starts with sk-or-v1- and is about 70 letters and numbers long." },
    is_it_free: "Yes, with its free models, which AiAlgoDesk uses by default. Free models have a low daily limit; adding a little credit on OpenRouter raises it. The free models are run by other companies that may keep and use what you send, so chart analysis waits until you confirm your key is on a paid plan (then AiAlgoDesk uses paid models, billed by OpenRouter). OpenRouter's pages have the current limits.",
    errors: [
      { problem: "No auth credentials found or invalid key", fix: "Copy the whole key again (it starts with sk-or-) and paste it here.", match: /auth|invalid|401|credentials/i },
      { problem: "This request requires more credits", fix: "Add credit on OpenRouter, or wait: the free daily limit starts again tomorrow.", match: /credit|402|payment/i },
      { problem: "Rate limit exceeded", fix: "The free models' limit is used up. Try again in a minute, or tomorrow for the daily limit.", match: /rate|429|limit/i },
    ],
  },
  {
    provider: "groq",
    name: "Groq",
    cost: "Free tier",
    recommended: false,
    key_page: { label: "Open Groq key page", url: "https://console.groq.com/keys" },
    checked_on: "2026-10-22",
    steps: [
      { text: "Press Open Groq key page and sign in to GroqCloud, or create an account.", screenshot: "The GroqCloud sign-in page" },
      { text: "Press Create API Key. Name it AiAlgoDesk.", screenshot: "The Create API Key button in GroqCloud" },
      { text: "Copy the key now: Groq shows it only once.", screenshot: "A new key with its copy button (key hidden)" },
      PASTE_STEP,
    ],
    looks_like: { prefix: "gsk_", text: "Starts with gsk_ and is about 56 letters and numbers long." },
    is_it_free: "Yes, to start. GroqCloud has a free plan with limits on how much you can send each minute and each day. Groq answers fast but does not read charts here, so chart analysis needs another key. Groq's limits page has the current numbers.",
    errors: [
      { problem: "Invalid API Key", fix: "Copy the whole key again (it starts with gsk_) and paste it here.", match: /invalid|401|api key/i },
      { problem: "Rate limit reached", fix: "The free plan's limit is used up for now. Try again in a minute.", match: /rate|429|limit/i },
      { problem: "The model has been decommissioned", fix: "Groq retired a model. AiAlgoDesk moves to a new one in an update; try again later.", match: /decommission|model/i },
    ],
  },
  {
    provider: "mistral",
    name: "Mistral",
    cost: "Free tier",
    recommended: false,
    key_page: { label: "Open Mistral key page", url: "https://console.mistral.ai/api-keys" },
    checked_on: "2026-10-22",
    steps: [
      { text: "Press Open Mistral key page and sign in to Mistral Studio, or create an account.", screenshot: "The Mistral Studio sign-in page" },
      { text: "Under API Keys, press Create new key. Name it AiAlgoDesk and pick when it should expire.", screenshot: "The Create new key button in Mistral Studio" },
      { text: "Copy the key now: Mistral shows it only once.", screenshot: "A new key with its copy button (key hidden)" },
      { text: "Come back to this page, paste the key in the box below and pick Mistral when AiAlgoDesk asks which service it is from." },
    ],
    looks_like: { prefix: "", text: "A row of about 32 letters and numbers, with no fixed start. AiAlgoDesk asks which service it is from." },
    is_it_free: "Yes, to start. Mistral's free mode needs no card and has usage and rate limits. On the free mode Mistral may use what you send to improve its models unless you opt out in its settings, so chart analysis waits until you confirm your key is on a paid plan.",
    errors: [
      { problem: "Unauthorized", fix: "Copy the whole key again and paste it here. A key that has expired must be created again.", match: /unauthori[sz]ed|401|invalid/i },
      { problem: "Rate limit exceeded", fix: "The free mode's limit is used up for now. Try again in a minute.", match: /rate|429|limit/i },
    ],
  },
  {
    provider: "zai",
    name: "Z.ai GLM",
    cost: "Free tier",
    recommended: false,
    key_page: { label: "Open Z.ai key page", url: "https://z.ai/manage-apikey/apikey-list" },
    checked_on: "2026-10-22",
    steps: [
      { text: "Press Open Z.ai key page and sign in to the Z.ai platform, or create an account.", screenshot: "The Z.ai sign-in page" },
      { text: "On the API Keys page, create a standard API key (not a Coding Plan key). Name it AiAlgoDesk.", screenshot: "The create key button on Z.ai" },
      { text: "Copy the key.", screenshot: "A new key with its copy button (key hidden)" },
      { text: "Come back to this page, paste the key in the box below and pick Z.ai GLM when AiAlgoDesk asks which service it is from." },
    ],
    looks_like: { prefix: "", text: "Two parts of letters and numbers joined by one dot, with no fixed start. AiAlgoDesk asks which service it is from." },
    is_it_free: "Partly. Its GLM Flash model is free and AiAlgoDesk uses it for the quick answers; the larger model needs balance on your Z.ai account. Z.ai does not read charts here, so chart analysis needs another key.",
    errors: [
      { problem: "Authentication failed", fix: "Copy the whole key again, both parts and the dot, and paste it here.", match: /auth|401|invalid/i },
      { problem: "Insufficient balance or no resource package", fix: "Add balance on the Z.ai billing page; the free Flash model keeps working without it.", match: /balance|recharge|1113|credit/i },
      { problem: "Rate limit reached for requests", fix: "Wait a minute and try again.", match: /rate|429|1302/i },
    ],
  },
];

/** The guides the Vision card shows (no `ai_one_key`): the three providers it takes, as before MO2 part A. */
export const VISION_CARD_GUIDES: readonly KeyGuide[] = KEY_GUIDES.filter((g) => g.provider === "gemini" || g.provider === "anthropic" || g.provider === "openai");

export function guideFor(provider: AiProviderName): KeyGuide {
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
