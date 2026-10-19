import { useEffect, useState } from "react";

/**
 * The Copilot's language settings, stored per browser: the interface locale (English only - the owner's ruling) and the
 * strategy interview's second-language line ("mr" or "off"; the Hindi bundle adds "hi" to SECONDARY_LOCALES and an
 * INTERVIEW_HI object to i18n/interviewSecondary.ts). Only the interview screen reads or switches it.
 */
export const UI_LOCALES = ["en"] as const;
export type UiLocale = (typeof UI_LOCALES)[number];
export const SECONDARY_LOCALES = ["mr"] as const;
export type SecondaryLocale = (typeof SECONDARY_LOCALES)[number] | "off";
export interface CopilotLanguage { ui: UiLocale; secondary: SecondaryLocale }

const KEY = "atp_copilot_lang";
export const LANGUAGE_EVENT = "atp-copilot-lang";
const DEFAULT: CopilotLanguage = { ui: "en", secondary: "mr" };

export function loadCopilotLanguage(): CopilotLanguage {
  try {
    const v = JSON.parse(localStorage.getItem(KEY) ?? "{}") as Partial<CopilotLanguage>;
    const ui = (UI_LOCALES as readonly string[]).includes(v.ui ?? "") ? (v.ui as UiLocale) : DEFAULT.ui;
    const secondary = v.secondary === "off" || (SECONDARY_LOCALES as readonly string[]).includes(v.secondary ?? "") ? (v.secondary as SecondaryLocale) : DEFAULT.secondary;
    return { ui, secondary };
  } catch {
    return DEFAULT;
  }
}

export function saveCopilotLanguage(l: CopilotLanguage): void {
  try { localStorage.setItem(KEY, JSON.stringify(l)); } catch { /* storage unavailable: this tab only */ }
  window.dispatchEvent(new CustomEvent(LANGUAGE_EVENT));
}

export function useCopilotLanguage(): [CopilotLanguage, (l: CopilotLanguage) => void] {
  const [l, setL] = useState(loadCopilotLanguage);
  useEffect(() => {
    const on = () => setL(loadCopilotLanguage());
    window.addEventListener(LANGUAGE_EVENT, on);
    window.addEventListener("storage", on);
    return () => { window.removeEventListener(LANGUAGE_EVENT, on); window.removeEventListener("storage", on); };
  }, []);
  return [l, saveCopilotLanguage];
}
