import { createInstance, type TFunction } from "i18next";
import { initReactI18next, useTranslation } from "react-i18next";
import { PORTFOLIO_RISK_EN } from "./locales/en";

/**
 * N1 F3: the portfolio risk card's text, through i18n keys (react-i18next, as the Copilot does). English is the only
 * interface locale for now; a separate instance, so nothing outside the Positions page pays for it.
 */
export const portfolioI18n = createInstance();
void portfolioI18n.use(initReactI18next).init({
  lng: "en",
  fallbackLng: "en",
  ns: ["portfolioRisk"],
  defaultNS: "portfolioRisk",
  resources: { en: { portfolioRisk: PORTFOLIO_RISK_EN } },
  interpolation: { escapeValue: false },     // React escapes
  returnNull: false,
  initAsync: false,                          // resources are inline: ready synchronously
});

export type PortfolioT = TFunction<"portfolioRisk">;

export function usePortfolioT(): PortfolioT {
  return useTranslation("portfolioRisk", { i18n: portfolioI18n }).t;
}

/** The same strings outside React (pure helpers and tests). */
export const portfolioT: PortfolioT = portfolioI18n.getFixedT("en", "portfolioRisk");
