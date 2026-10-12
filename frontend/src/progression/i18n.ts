import { createInstance, type TFunction } from "i18next";
import { initReactI18next, useTranslation } from "react-i18next";
import { PROGRESSION_EN } from "./locales/en";

/**
 * N2 (P1-f/g/h) interface strings through i18n keys - the Copilot's pattern (copilot/i18n.ts): its own i18next
 * instance, namespace `progression`, English the only interface locale for now. A separate instance, so only the
 * pages that show the ladder evidence pay for it.
 */
export const progressionI18n = createInstance();
void progressionI18n.use(initReactI18next).init({
  lng: "en",
  fallbackLng: "en",
  ns: ["progression"],
  defaultNS: "progression",
  resources: { en: { progression: PROGRESSION_EN } },
  interpolation: { escapeValue: false },     // React escapes
  returnNull: false,
  initAsync: false,                          // resources are inline: ready synchronously
});

/** The progression strings, by key: t("evidence.title"). */
export function useProgressionT(): TFunction<"progression"> {
  return useTranslation("progression", { i18n: progressionI18n }).t;
}

/** Outside React (helpers, tests). */
export const tProgression: TFunction<"progression"> = progressionI18n.getFixedT("en", "progression");
