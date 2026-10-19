import { Button } from "../../components/primitives";
import { useCopilotT } from "../i18n";
import { SECONDARY_LOCALES, type SecondaryLocale, useCopilotLanguage } from "../languagePrefs";

/**
 * The strategy interview's second-language line, chosen on the interview screen itself (the owner's ruling: ATP is
 * English only, and nothing outside the interview may offer Marathi / Hindi - so the switch is not in Settings). The
 * labels are English interface strings; the second-language lines come from the `interview` namespace.
 */
export default function InterviewLanguageSwitch() {
  const t = useCopilotT();
  const [lang, save] = useCopilotLanguage();
  const options: SecondaryLocale[] = [...SECONDARY_LOCALES, "off"];
  return (
    <div role="group" aria-label={t("idea.secondLine")} className="flex flex-wrap items-center gap-1.5 text-xs text-fg-muted" data-testid="interview-language">
      <span>{t("idea.secondLine")}</span>
      {options.map((o) => (
        <Button key={o} size="sm" variant={lang.secondary === o ? "primary" : "secondary"} aria-pressed={lang.secondary === o}
          onClick={() => save({ ...lang, secondary: o })}>{t(`idea.secondLineOption.${o}`)}</Button>
      ))}
    </div>
  );
}
