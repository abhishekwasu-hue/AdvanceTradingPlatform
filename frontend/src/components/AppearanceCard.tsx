import { Moon, Sun } from "lucide-react";
import { useAppearance, type ThemeChoice } from "../theme";
import { Button } from "./primitives";
import { Card } from "./ui";

const THEMES: { value: ThemeChoice; label: string }[] = [
  { value: "dark", label: "Dark" }, { value: "light", label: "Light" }, { value: "system", label: "Follow the device" },
];

/** P1.2: theme and the colour-blind option, saved in this browser. */
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
