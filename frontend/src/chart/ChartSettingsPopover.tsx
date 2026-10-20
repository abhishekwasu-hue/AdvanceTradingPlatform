import { Check, Settings2 } from "lucide-react";
import { useEffect, useId, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type ReactNode } from "react";
import { CANDLE_PRESETS, type ChartThemeChoice } from "./chartTheme";
import type { ChartPrefs, ChartPrefsPatch } from "./useChartPrefs";

/**
 * UI2-4b1 (Charts page plan §3, CHP-11 / 12): the one chart settings place, the same in every v2 chart - chart theme
 * (App / Light / Dark), grid on or off, candle colours (presets, and a reset to the theme's own). A change applies to
 * every chart at once and is saved per user (`useChartPrefs`). Escape, a click outside or the button again closes it.
 */
const THEMES: { id: ChartThemeChoice; label: string }[] = [
  { id: "app", label: "App" }, { id: "light", label: "Light" }, { id: "dark", label: "Dark" },
];

const cx = (...c: (string | false)[]) => c.filter(Boolean).join(" ");
const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

function Swatch({ color }: { color: string }) {
  return <span aria-hidden className="inline-block h-3 w-3 rounded-pill border border-subtle" style={{ backgroundColor: color }} />;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="space-y-space-2">
      <p className="text-t12 font-medium text-fg-muted">{title}</p>
      {children}
    </div>
  );
}

/** Arrow keys move the choice within a radio group (WAI-ARIA radio pattern: one tab stop, the checked option). */
export function radioKeys<T>(ids: readonly T[], current: T | undefined, pick: (id: T) => void) {
  return (e: ReactKeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const at = current === undefined ? -1 : ids.indexOf(current);
    const next = (Math.max(at, step > 0 ? -1 : 0) + step + ids.length) % ids.length;
    pick(ids[next]);
    e.currentTarget.querySelectorAll<HTMLElement>('[role="radio"]')[next]?.focus();
  };
}

export function ChartSettingsPopover({ prefs, onChange, error, defaultOpen = false }: {
  prefs: ChartPrefs; onChange: (patch: ChartPrefsPatch) => void; error?: string | null; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    const onDown = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  }, [open]);
  const same = (a?: string | null, b?: string | null) => (a ?? "").toLowerCase() === (b ?? "").toLowerCase();
  const preset = CANDLE_PRESETS.find((p) => same(p.colors?.up, prefs.candle_colors?.up) && same(p.colors?.down, prefs.candle_colors?.down))?.id;

  return (
    <div ref={ref} className="relative inline-flex">
      <button type="button" aria-label="Chart settings" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen((o) => !o)}
        className={cx("inline-flex min-h-11 min-w-11 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg md:min-h-9 md:min-w-9", focus)}>
        <Settings2 size={16} aria-hidden />
      </button>
      {open && (
        <div id={id} role="dialog" aria-label="Chart settings"
          className="absolute right-0 top-full z-30 mt-space-1 w-72 max-w-[calc(100vw-2rem)] space-y-space-4 rounded-panel border border-subtle bg-surface-1 p-space-4 text-t14 text-fg shadow-raised">
          <Section title="Chart theme">
            <div role="radiogroup" aria-label="Chart theme" className="grid grid-cols-3 gap-space-1 rounded-panel bg-surface-2 p-space-1"
              onKeyDown={radioKeys(THEMES.map((t) => t.id), prefs.theme, (theme) => onChange({ theme }))}>
              {THEMES.map((t) => (
                <button key={t.id} type="button" role="radio" aria-checked={prefs.theme === t.id} tabIndex={prefs.theme === t.id ? 0 : -1}
                  onClick={() => onChange({ theme: t.id })}
                  className={cx("min-h-11 rounded-control px-space-2 md:min-h-9", focus,
                    prefs.theme === t.id ? "bg-surface-1 font-semibold text-fg shadow-card" : "text-fg-muted hover:text-fg")}>
                  {t.label}
                </button>
              ))}
            </div>
            <p className="text-t12 text-fg-muted">App follows the app's theme.</p>
          </Section>
          <label className="flex min-h-11 cursor-pointer items-center justify-between gap-space-3 md:min-h-9">
            <span>Grid lines</span>
            <input type="checkbox" role="switch" checked={prefs.grid} onChange={(e) => onChange({ grid: e.target.checked })}
              className={cx("h-4 w-4 accent-brand", focus)} />
          </label>
          <Section title="Candle colours">
            <div role="radiogroup" aria-label="Candle colours" className="space-y-space-1"
              onKeyDown={radioKeys(CANDLE_PRESETS.map((p) => p.id), preset,
                (pid) => onChange({ candle_colors: CANDLE_PRESETS.find((p) => p.id === pid)?.colors ?? null }))}>
              {CANDLE_PRESETS.map((p, i) => (
                <button key={p.id} type="button" role="radio" aria-checked={preset === p.id}
                  tabIndex={preset === p.id || (preset === undefined && i === 0) ? 0 : -1} onClick={() => onChange({ candle_colors: p.colors })}
                  className={cx("flex min-h-11 w-full items-center gap-space-2 rounded-control px-space-2 text-left hover:bg-surface-2 md:min-h-9", focus)}>
                  <Swatch color={p.colors?.up ?? "rgb(var(--chart-up))"} />
                  <Swatch color={p.colors?.down ?? "rgb(var(--chart-down))"} />
                  <span className="flex-1">{p.label}</span>
                  {preset === p.id && <Check size={16} aria-hidden className="text-fg" />}
                </button>
              ))}
            </div>
            <button type="button" disabled={prefs.candle_colors == null} onClick={() => onChange({ candle_colors: null })}
              className={cx("min-h-11 rounded-control px-space-2 text-t13 text-fg-muted hover:text-fg disabled:opacity-40 md:min-h-9", focus)}>
              Reset colours
            </button>
          </Section>
          {error && <p role="status" className="text-t12 text-fg-muted">{error}</p>}
        </div>
      )}
    </div>
  );
}
