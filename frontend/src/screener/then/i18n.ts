import { createInstance, type TFunction } from "i18next";
import { initReactI18next, useTranslation } from "react-i18next";
import { SCAN_THEN_EN } from "./locales/en";

/**
 * N1 X1b: the scan Then text, through i18n keys (react-i18next, as the Copilot does). English is the only interface
 * locale for now; a separate instance, so nothing outside the alerts screen pays for it.
 */
export const thenI18n = createInstance();
void thenI18n.use(initReactI18next).init({
  lng: "en",
  fallbackLng: "en",
  ns: ["scanThen"],
  defaultNS: "scanThen",
  resources: { en: { scanThen: SCAN_THEN_EN } },
  interpolation: { escapeValue: false },     // React escapes
  returnNull: false,
  initAsync: false,                          // resources are inline: ready synchronously
});

export type ThenT = TFunction<"scanThen">;

/** The Then strings for components: t("drawer.title"). */
export function useThenT(): ThenT {
  return useTranslation("scanThen", { i18n: thenI18n }).t;
}

/** The same strings outside React (pure helpers and tests). */
export const thenT: ThenT = thenI18n.getFixedT("en", "scanThen");
