import { Moon, Sun } from "lucide-react";
import { useCopilotLanguage, type SecondaryLocale } from "../copilot/languagePrefs";
import { useAppearance, type ThemeChoice } from "../theme";
import { Button } from "./primitives";
import { Card } from "./ui";

const THEMES: { value: ThemeChoice; label: string }[] = [
  { value: "dark", label: "Dark" }, { value: "light", label: "Light" }, { value: "system", label: "Follow the device" },
];

const SECONDARY_OPTIONS: { value: SecondaryLocale; label: string }[] = [{ value: "mr", label: "Marathi" }, { value: "off", label: "None" }];

/** The AI Copilot's languages: the interface (English; more locales later) and the interview's second line. */
function CopilotLanguageRows() {
  const [lang, save] = useCopilotLanguage();
  return (
    <div role="group" aria-labelledby="copilot-lang-label" className="border-t border-border pt-3">
      <div id="copilot-lang-label" className="mb-1 text-xs font-medium text-fg-muted">AI Copilot languages</div>
      <p className="mb-2 text-xs text-fg-muted">Interface: English. The strategy interview shows each question in English with a small second line in:</p>
      <div className="flex flex-wrap gap-2">
        {SECONDARY_OPTIONS.map((o) => (
          <Button key={o.value} size="sm" variant={lang.secondary === o.value ? "primary" : "secondary"} aria-pressed={lang.secondary === o.value}
            onClick={() => save({ ...lang, secondary: o.value })}>{o.label}</Button>
        ))}
      </div>
    </div>
  );
}

/** P1.2: theme and the colour-blind option, saved in this browser. Copilot redesign: reduce motion, interview language. */
export default function AppearanceCard() {
  const [a, save] = useAppearance();
  return (
    <Card title="Appearance">
      <div className="space-y-4">
        <div role="group" aria-labelledby="appearance-theme-label">
          <div id="appearance-theme-label" className="mb-2 text-xs font-medium text-fg-muted">Theme</div>
          <div className="flex flex-wrap gap-2">
            {THEMES.map((t) => (
              <Button key={t.value} size="sm" variant={a.theme === t.value ? "primary" : "secondary"} aria-pressed={a.theme === t.value}
                onClick={() => save({ ...a, theme: t.value })}>{t.label}</Button>
            ))}
          </div>
        </div>
        <label className="flex items-start gap-3 text-sm text-fg">
          <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[rgb(var(--brand))]" checked={a.colorBlind}
            onChange={(e) => save({ ...a, colorBlind: e.target.checked })} />
          <span>
            Colour-blind friendly profit and loss
            <span className="mt-0.5 block text-xs text-fg-muted">Profit shows blue and loss orange instead of green and red. Signs (+ / -) are always shown too.</span>
          </span>
        </label>
        <div className="flex items-center gap-3 text-xs text-fg-muted" aria-hidden>
          <span className="font-tabular text-up">+1,250.00</span><span className="font-tabular text-down">-830.50</span>
          <span>preview</span>
        </div>
        <label className="flex items-start gap-3 text-sm text-fg">
          <input type="checkbox" className="mt-0.5 h-4 w-4 accent-[rgb(var(--brand))]" checked={a.reduceMotion}
            onChange={(e) => save({ ...a, reduceMotion: e.target.checked })} />
          <span>
            Reduce motion
            <span className="mt-0.5 block text-xs text-fg-muted">Turns off animations, card tilt and the AI Copilot's 3D scene (a still image is shown). The device's own reduced-motion setting does the same.</span>
          </span>
        </label>
        <CopilotLanguageRows />
      </div>
    </Card>
  );
}

/** The top-bar switch between dark and light (a "follow the device" choice lives in Settings). */
export function ThemeToggle() {
  const [a, save, resolved] = useAppearance();
  const next = resolved === "dark" ? "light" : "dark";
  return (
    <button onClick={() => save({ ...a, theme: next })} title={`Switch to the ${next} theme`} aria-label={`Switch to the ${next} theme`}
      className="rounded-md p-2 text-fg-muted transition-colors hover:bg-surface-2 hover:text-fg">
      {resolved === "dark" ? <Sun size={18} /> : <Moon size={18} />}
    </button>
  );
}
