import { createInstance, type TFunction } from "i18next";
import { initReactI18next, useTranslation } from "react-i18next";
import { INTERVIEW_MR } from "../i18n/interviewSecondary";
import { type SecondaryLocale, useCopilotLanguage } from "./languagePrefs";
import { COPILOT_EN } from "./locales/en";

/**
 * The Copilot's text, through i18n keys (react-i18next; the app-wide move in P1.7 reuses these resources).
 *
 * - `copilot` namespace: every interface string of the Copilot page - English, the only interface locale for now.
 * - `interview` namespace: the strategy interview's muted second-language lines. The secondary language is a setting
 *   (default Marathi, or off); a Hindi bundle is one more entry in SECONDARY_BUNDLES.
 *
 * Devanagari may appear only in the `interview` namespace (scripts/check-devanagari.mjs and i18n.test.ts).
 * A separate i18next instance, so nothing outside the Copilot chunk pays for it.
 */
export const SECONDARY_BUNDLES: Record<Exclude<SecondaryLocale, "off">, typeof INTERVIEW_MR> = { mr: INTERVIEW_MR };

export const copilotI18n = createInstance();
void copilotI18n.use(initReactI18next).init({
  lng: "en",
  fallbackLng: "en",
  ns: ["copilot", "interview"],
  defaultNS: "copilot",
  resources: {
    en: { copilot: COPILOT_EN },
    ...Object.fromEntries(Object.entries(SECONDARY_BUNDLES).map(([lng, bundle]) => [lng, { interview: bundle }])),
  },
  interpolation: { escapeValue: false },     // React escapes
  returnNull: false,
  initAsync: false,                          // resources are inline: ready synchronously
});

/** The Copilot interface strings, by key: t("pulse.todayRead"). */
export function useCopilotT(): TFunction<"copilot"> {
  return useTranslation("copilot", { i18n: copilotI18n }).t;
}

export type InterviewKey = keyof typeof INTERVIEW_MR;

/** The interview's second-language line for a key, or "" when the second language is off. */
export function useSecondary(): { lang: Exclude<SecondaryLocale, "off"> | null; line: (key: InterviewKey) => string } {
  const [l] = useCopilotLanguage();
  const lang = l.secondary === "off" ? null : l.secondary;
  return {
    lang,
    line: (key) => (lang ? copilotI18n.getFixedT(lang, "interview")(key) : ""),
  };
}
