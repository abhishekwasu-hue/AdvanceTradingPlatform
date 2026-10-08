import { useEffect, useState } from "react";

/** P1.2: the appearance settings - theme (dark / light / follow the system) and the colour-blind option (profit
 * blue, loss orange). Stored per browser; applied as `data-theme` / `data-cvd` on <html>, which tokens.css reads.
 * A change fires `atp-theme` so canvases that cannot read CSS (the charts) repaint. */
export type ThemeChoice = "dark" | "light" | "system";
export interface Appearance { theme: ThemeChoice; colorBlind: boolean }

const KEY = "atp_appearance";
const EVENT = "atp-theme";
const DEFAULTS: Appearance = { theme: "dark", colorBlind: false };

export function loadAppearance(): Appearance {
  try {
    const raw = localStorage.getItem(KEY);
    const v = raw ? (JSON.parse(raw) as Partial<Appearance>) : {};
    const theme: ThemeChoice = v.theme === "light" || v.theme === "system" || v.theme === "dark" ? v.theme : DEFAULTS.theme;
    return { theme, colorBlind: v.colorBlind === true };
  } catch {
    return DEFAULTS;
  }
}

function prefersLight(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
}

export function resolvedTheme(a: Appearance = loadAppearance()): "dark" | "light" {
  return a.theme === "system" ? (prefersLight() ? "light" : "dark") : a.theme;
}

export function applyAppearance(a: Appearance = loadAppearance()): void {
  const root = document.documentElement;
  const theme = resolvedTheme(a);
  root.dataset.theme = theme;
  root.dataset.cvd = a.colorBlind ? "on" : "off";
  root.classList.toggle("dark", theme === "dark");
  window.dispatchEvent(new CustomEvent(EVENT));
}

export function saveAppearance(a: Appearance): void {
  try { localStorage.setItem(KEY, JSON.stringify(a)); } catch { /* storage unavailable: this tab only */ }
  applyAppearance(a);
}

/** Applies the saved appearance now and follows the system theme when the trader chose "system". */
export function initAppearance(): void {
  applyAppearance();
  // Another tab changed the appearance: follow it here too.
  window.addEventListener("storage", (e) => { if (e.key === KEY) applyAppearance(); });
  window.matchMedia?.("(prefers-color-scheme: light)").addEventListener?.("change", () => {
    if (loadAppearance().theme === "system") applyAppearance();
  });
}

/** The current appearance, re-rendering on every change (any tab component, the charts). */
export function useAppearance(): [Appearance, (a: Appearance) => void, "dark" | "light"] {
  const [a, setA] = useState<Appearance>(loadAppearance);
  const [resolved, setResolved] = useState(() => resolvedTheme(a));
  useEffect(() => {
    const on = () => { const next = loadAppearance(); setA(next); setResolved(resolvedTheme(next)); };
    window.addEventListener(EVENT, on);
    return () => window.removeEventListener(EVENT, on);
  }, []);
  return [a, saveAppearance, resolved];
}

/** A token's colour as a CSS colour string, read from the document (for canvases). */
export function tokenColor(name: string, alpha = 1): string {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
  return raw ? `rgb(${raw.split(/\s+/).join(", ")}${alpha < 1 ? `, ${alpha}` : ""})`.replace("rgb(", alpha < 1 ? "rgba(" : "rgb(") : "";
}

// The dark-theme values, used when the stylesheet has not loaded (tests, a detached window).
const FALLBACK = { text: "rgb(194, 202, 216)", grid: "rgb(26, 35, 51)", border: "rgb(36, 44, 63)", fg: "rgb(241, 245, 249)",
                   up: "rgb(34, 197, 94)", down: "rgb(239, 68, 68)", upSoft: "rgba(34, 197, 94, 0.45)", downSoft: "rgba(239, 68, 68, 0.45)" };

/** The chart colours of the current theme. */
export function chartColors() {
  const t = (name: string, fallback: string, alpha = 1) => tokenColor(name, alpha) || fallback;
  return {
    text: t("chart-text", FALLBACK.text), grid: t("chart-grid", FALLBACK.grid), border: t("border", FALLBACK.border),
    fg: t("fg", FALLBACK.fg), up: t("up", FALLBACK.up), down: t("down", FALLBACK.down),
    upSoft: t("up", FALLBACK.upSoft, 0.45), downSoft: t("down", FALLBACK.downSoft, 0.45),
  };
}

/** Chart overlays (price lines, markers) name a meaning instead of a hex so a theme or colour-blind change recolours
 * them: "up" (profit, target, bullish), "down" (loss, stop, bearish), "fg" (neutral, e.g. the entry line). */
export type ChartTone = "up" | "down" | "fg";
export function resolveChartColor(color: string): string {
  return color === "up" || color === "down" || color === "fg" ? chartColors()[color] : color;
}

export const THEME_EVENT = EVENT;
