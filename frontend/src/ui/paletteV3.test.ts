import { describe, expect, it } from "vitest";
import css from "../styles/tokens.css?raw";

/**
 * UX-14 "Professional palette" (owner, 28 Oct; docs/owner-orders/ATP_ORDER_UX14.md): the colour v3 contrast numbers,
 * computed from tokens.css itself (WCAG 2.x relative luminance), dark + light and both colour-blind modes:
 * - text >= 4.5 : 1 on every surface (page, card, raised): body, muted labels, links (brand), up / down / warn, the AI badge;
 * - things that carry meaning >= 3 : 1 against their surface: the primary button fill, the input / chip border;
 * - white on the primary button >= 4.5 : 1; elevation by surface steps (page -> card -> raised, each a different colour).
 */
function block(selector: string): Record<string, number[]> {
  const re = new RegExp(`(?:^|\\n)${selector.replace(/[[\]"=()]/g, (c) => `\\${c}`)}\\s*\\{([^}]*)\\}`, "g");
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

const base = block(":root");
const v3Dark = { ...base, ...block(':root[data-colour="v2"]:not([data-theme="light"])'), ...block(':root[data-palette="v3"]:not([data-theme="light"])') };
const v3Light = { ...base, ...block(':root[data-theme="light"]'), ...block(':root[data-colour="v2"][data-theme="light"]'),
  ...block(':root[data-palette="v3"][data-theme="light"]') };
const cvdDark = block(':root[data-palette="v3"][data-cvd="on"]:not([data-theme="light"])');
const cvdLight = block(':root[data-palette="v3"][data-cvd="on"][data-theme="light"]');
const THEMES: Record<string, Record<string, number[]>> = {
  "v3 dark": v3Dark,
  "v3 light": v3Light,
  "v3 colour-blind dark": { ...v3Dark, ...cvdDark },
  "v3 colour-blind light": { ...v3Light, ...cvdLight },
};
const SURFACES = ["surface", "surface-1", "surface-2"];
const TEXT = ["fg", "fg-muted", "brand", "up", "down", "warn", "ai", "info"];
const WHITE = [255, 255, 255];

describe("UX-14 colour v3 contrast (computed from tokens.css)", () => {
  it("the ordered surfaces, one accent and the AI badge colour", () => {
    expect(v3Dark.surface).toEqual([11, 14, 20]);
    expect(v3Dark["surface-1"]).toEqual([19, 23, 34]);
    expect(v3Dark["brand-fill"]).toEqual([41, 98, 255]);
    expect(v3Light["surface-1"]).toEqual([255, 255, 255]);
    // The extra accents point at the brand or a meaning colour (the tokens stay).
    for (const t of [v3Dark, v3Light]) {
      expect(t["accent-2"]).toEqual(t.brand);
      expect(t.signal).toEqual(t.brand);
      expect(t.armed).toEqual(t.warn);
      expect(t["ai-2"]).toEqual(t.ai);
      for (const tone of ["tone-profit", "tone-loss", "tone-risk", "tone-info"]) expect(t[tone]).toEqual(t["surface-2"]);
    }
  });

  it.each(Object.entries(THEMES))("%s: text >= 4.5 : 1 on every surface", (_, t) => {
    for (const fg of TEXT) for (const s of SURFACES) {
      expect(contrast(t[fg], t[s]), `${fg} on ${s}`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it.each(Object.entries(THEMES))("%s: the button, borders that carry meaning and the surface steps", (_, t) => {
    expect(contrast(WHITE, t["brand-fill"]), "white on the primary button").toBeGreaterThanOrEqual(4.5);
    for (const s of SURFACES) {
      expect(contrast(t["brand-fill"], t[s]), `primary button on ${s}`).toBeGreaterThanOrEqual(3);
      expect(contrast(t.border, t[s]), `input / chip border on ${s}`).toBeGreaterThanOrEqual(3);
    }
    expect(new Set(SURFACES.map((s) => t[s].join(" "))).size, "page, card and raised differ").toBe(3);
  });

  it("no dot backdrop, gradient or gradient outline under colour v3", () => {
    expect(css).toMatch(/:root\[data-palette="v3"\] \.dot-backdrop \{ display: none; \}/);
    expect(css).toMatch(/:root\[data-palette="v3"\] \{ --accent-gradient: linear-gradient\(rgb\(var\(--brand-fill\)\), rgb\(var\(--brand-fill\)\)\); \}/);
    expect(css).toMatch(/:root\[data-palette="v3"\] \.accent-gradient-border \{/);
  });
});
