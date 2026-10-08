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

/** The chart colours of the current theme. */
export function chartColors() {
  return {
    text: tokenColor("chart-text"), grid: tokenColor("chart-grid"), border: tokenColor("border"),
    up: tokenColor("up"), down: tokenColor("down"), upSoft: tokenColor("up", 0.45), downSoft: tokenColor("down", 0.45),
  };
}

export const THEME_EVENT = EVENT;
