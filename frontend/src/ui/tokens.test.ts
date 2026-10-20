import { describe, expect, it } from "vitest";
import css from "../styles/tokens.css?raw";   // vite.config.ts lets the unit tests read this one stylesheet

/**
 * UI2-0 (UI v2 proposal §8 "Accessibility"): every text / background pair a v2 screen uses meets WCAG AA in both
 * themes and in the colour-blind variant, computed from tokens.css itself - so a later token change cannot break it
 * silently. Body text needs 4.5:1; large figures (20 px+ or 14 px+ bold) and icons need 3:1.
 */
function block(selector: string): Record<string, number[]> {
  const re = new RegExp(`${selector.replace(/[[\]"=]/g, (c) => `\\${c}`)}\\s*\\{([^}]*)\\}`, "g");
  const out: Record<string, number[]> = {};
  for (const m of css.matchAll(re)) {
    for (const [, name, value] of m[1].matchAll(/--([\w-]+):\s*([\d\s]+);/g)) out[name] = value.trim().split(/\s+/).map(Number);
  }
  return out;
}

function luminance([r, g, b]: number[]): number {
  const ch = (v: number) => { const s = v / 255; return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
  return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
}
function contrast(a: number[], b: number[]): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

const dark = block(":root");
const light = { ...dark, ...block(':root[data-theme="light"]') };
const cvdDark = { ...dark, ...block(':root[data-cvd="on"]') };
const cvdLight = { ...light, ...block(':root[data-theme="light"][data-cvd="on"]') };
const THEMES = { dark, light, cvdDark, cvdLight };

/** [text, background, minimum ratio]: the pairs v2 uses (proposal §8 colour table). */
const PAIRS: [string, string, number][] = [
  // Text and captions on every surface (fg-muted is "AA on every surface"), and on a warning note.
  ...["surface", "surface-1", "surface-2", "surface-3", "warn-tint"].flatMap((bg): [string, string, number][] => [
    ["fg", bg, 4.5], ["fg-muted", bg, 4.5],
  ]),
  // Meaning colours as body text (P&L, Long / Short, links, warnings) on the page and on cards.
  ...["surface", "surface-1"].flatMap((bg): [string, string, number][] => [
    ["brand", bg, 4.5], ["up", bg, 4.5], ["down", bg, 4.5], ["warn", bg, 4.5],
  ]),
  // On chips and hovered rows (surface-2) the meaning colours are used for large figures and icons only.
  ["brand", "surface-2", 3], ["up", "surface-2", 3], ["down", "surface-2", 3], ["warn", "surface-2", 3],
  // A warning note: amber text on its tint.
  ["warn", "warn-tint", 4.5],
  // Primary buttons: white on the brand fill, at rest and on hover.
  ["on-brand", "brand-fill", 4.5], ["on-brand", "brand-fill-strong", 4.5],
];

describe("UI2-0 v2 tokens", () => {
  it.each(Object.entries(THEMES))("%s: every v2 text / background pair meets WCAG AA", (_, t) => {
    for (const [fg, bg, min] of PAIRS) {
      expect(t[fg], `--${fg} defined`).toBeDefined();
      expect(t[bg], `--${bg} defined`).toBeDefined();
      expect(contrast(t[fg], t[bg]), `${fg} on ${bg}`).toBeGreaterThanOrEqual(min);
    }
  });

  it("adds border-subtle and warn-tint in both themes, without changing an existing token", () => {
    expect(dark["border-subtle"]).toEqual([26, 33, 48]);
    expect(light["border-subtle"]).toEqual([229, 233, 240]);
    expect(dark["warn-tint"]).toEqual([41, 30, 10]);
    expect(light["warn-tint"]).toEqual([255, 251, 235]);
    // The values the proposal's colour table reads as "existing" (§8): unchanged.
    expect(light.surface).toEqual([244, 246, 249]);
    expect(light["surface-1"]).toEqual([255, 255, 255]);
    expect(light.border).toEqual([211, 217, 227]);
    expect(light.fg).toEqual([15, 23, 42]);
    expect(dark.surface).toEqual([9, 12, 18]);
    expect(dark.brand).toEqual([59, 130, 246]);
  });

  it("the subtle outline is quieter than the input border, in both themes", () => {
    for (const t of [dark, light]) {
      expect(contrast(t["border-subtle"], t["surface-1"])).toBeLessThan(contrast(t.border, t["surface-1"]));
    }
  });

  it("defines the shape, spacing and motion tokens", () => {
    expect(css).toMatch(/--radius-pill:\s*999px/);
    expect(css).toMatch(/--shadow-raised:\s*0 8px 24px rgba\(0, 0, 0, 0\.5\)/);
    expect(css).toMatch(/--shadow-raised:\s*0 8px 24px rgba\(15, 23, 42, 0\.12\)/);
    for (const [n, px] of [[1, 4], [2, 8], [3, 12], [4, 16], [5, 20], [6, 24], [8, 32], [10, 40], [12, 48]]) {
      expect(css).toMatch(new RegExp(`--space-${n}:\\s*${px}px;`));
    }
    expect(css).toMatch(/--dur-fast:\s*120ms/);
    expect(css).toMatch(/--dur-base:\s*200ms/);
    expect(css).toMatch(/--ease:\s*cubic-bezier\(0\.2, 0, 0, 1\)/);
  });

  it("turns motion off when the trader or the system reduces it", () => {
    expect(css).toMatch(/:root\[data-motion="reduce"\]\s*\{\s*--dur-fast:\s*0ms;\s*--dur-base:\s*0ms;\s*\}/);
    expect(css).toMatch(/@media \(prefers-reduced-motion: reduce\)\s*\{\s*:root\s*\{\s*--dur-fast:\s*0ms;\s*--dur-base:\s*0ms;/);
  });
});
