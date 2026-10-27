import { Moon, Sun } from "lucide-react";
import { useState } from "react";
import { useOptionalAuth } from "../auth/AuthContext";
import { setHighContrast, setTextSize } from "../appearanceSync";
import { DENSITIES, TEXT_SIZES, useAppearance, type TextSize, type ThemeChoice } from "../theme";
import { useUiV2 } from "../ui/useFeatures";
import { Button } from "./primitives";
import { Card } from "./ui";

const THEMES: { value: ThemeChoice; label: string }[] = [
  { value: "dark", label: "Dark" }, { value: "light", label: "Light" }, { value: "system", label: "Follow the device" },
];

/** P1.2: theme and the colour-blind option, saved in this browser. Copilot redesign: reduce motion. Item 1: the text
 * size, saved for the user on the server so every device shows the same size. UI-H-DM DM4: High contrast, saved the same
 * way, beside the text size. ATP is English only:
 * the strategy interview's second-language line is switched on the interview screen, not here. */
export default function AppearanceCard() {
  const [a, save] = useAppearance();
  const user = useOptionalAuth()?.user ?? null;
  const [sizeNote, setSizeNote] = useState<string | null>(null);
  const v2 = useUiV2();
  const pickSize = async (size: TextSize) => {
    setSizeNote(null);
    const saved = await setTextSize(size, user != null);
    if (!saved) setSizeNote("Saved on this device only. Check your connection to keep it on your other devices.");
  };
  const pickContrast = async (on: boolean) => {
    setSizeNote(null);
    const saved = await setHighContrast(on, user != null);
    if (!saved) setSizeNote("Saved on this device only. Check your connection to keep it on your other devices.");
  };
  return (
    <Card title="Appearance">
      <div className="space-y-4">
        <div role="group" aria-labelledby="appearance-text-size-label">
          <div id="appearance-text-size-label" className="mb-2 text-xs font-medium text-fg-muted">Text size</div>
          <div className="flex flex-wrap gap-2">
            {TEXT_SIZES.map((t) => (
              <Button key={t.value} size="sm" variant={a.textSize === t.value ? "primary" : "secondary"} aria-pressed={a.textSize === t.value}
                onClick={() => void pickSize(t.value)}>{t.label}</Button>
            ))}
          </div>
          <p className="mt-1 text-xs text-fg-muted">Makes all text, numbers and charts bigger on every device you use.</p>
          {sizeNote && <p role="status" className="mt-1 text-xs text-warn">{sizeNote}</p>}
        </div>
        {/* UX-8: the redesigned pages' spacing (the space tokens), so only with ui_v2. Saved on this device. */}
        {v2 && (
          <div role="group" aria-labelledby="appearance-density-label" data-appearance="density">
            <div id="appearance-density-label" className="mb-2 text-xs font-medium text-fg-muted">Density</div>
            <div className="flex flex-wrap gap-2">
              {DENSITIES.map((d) => (
                <Button key={d.value} size="sm" variant={a.density === d.value ? "primary" : "secondary"} aria-pressed={a.density === d.value}
                  onClick={() => save({ ...a, density: d.value })}>{d.label}</Button>
              ))}
            </div>
            <p className="mt-1 text-xs text-fg-muted">Compact shows more on one screen: tighter rows and spacing. Buttons stay easy to tap.</p>
          </div>
        )}
        <label className="flex items-start gap-3 text-sm text-fg">
          <input type="checkbox" data-testid="appearance-high-contrast" className="mt-0.5 h-4 w-4 accent-[rgb(var(--brand))]"
            checked={a.highContrast} onChange={(e) => void pickContrast(e.target.checked)} />
          <span>
            High contrast
            <span className="mt-0.5 block text-xs text-fg-muted">Stronger borders and brighter text and icons - easier to read outdoors. Kept on every device you use.</span>
          </span>
        </label>
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
      className="flex h-11 w-11 items-center justify-center rounded-md text-fg-muted md:h-9 md:w-9 transition-colors hover:bg-surface-2 hover:text-fg">
      {resolved === "dark" ? <Sun size={18} /> : <Moon size={18} />}
    </button>
  );
}
