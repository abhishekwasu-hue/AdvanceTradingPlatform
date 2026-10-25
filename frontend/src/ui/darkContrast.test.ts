import { describe, expect, it } from "vitest";
import css from "../styles/tokens.css?raw";

/**
 * UI-H-DM DM5 (owner, 25 Oct; docs/owner-orders/ATP_ORDER_UI_H_DM.md): the contrast numbers of the colour v2 surfaces,
 * computed from tokens.css itself (WCAG 2.x relative luminance). Fails below any number the owner set:
 * - DM1 card vs page >= 1.3 : 1; raised (inputs, chips) a further step over a card (>= 1.15 : 1, dark);
 * - DM2 borders >= 3 : 1 on the surface they sit on (cards, inputs, the tab row line, the chart frame);
 * - DM3 icons, inactive tabs and nav labels >= 4.5 : 1; the primary button and the active tab >= 7 : 1.
 * Checked for colour v2 dark and light (light had failed the same checks), High contrast in both, and both
 * colour-blind modes. High contrast must be at least as strong as the normal theme.
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
const lightBase = { ...base, ...block(':root[data-theme="light"]') };
const v2Dark = { ...base, ...block(':root[data-colour="v2"]:not([data-theme="light"])') };
const v2Light = { ...lightBase, ...block(':root[data-colour="v2"][data-theme="light"]') };
const hcDark = { ...v2Dark, ...block(':root[data-contrast="high"]:not([data-theme="light"])') };
const hcLight = { ...v2Light, ...block(':root[data-contrast="high"][data-theme="light"]') };
const cvd = block(':root[data-cvd="on"]');
const cvdLight = block(':root[data-theme="light"][data-cvd="on"]');
const THEMES: Record<string, { t: Record<string, number[]>; dark: boolean }> = {
  "v2 dark": { t: v2Dark, dark: true },
  "v2 light": { t: v2Light, dark: false },
  "high contrast dark": { t: hcDark, dark: true },
  "high contrast light": { t: hcLight, dark: false },
  "colour-blind dark": { t: { ...v2Dark, ...cvd }, dark: true },
  "colour-blind light": { t: { ...v2Light, ...cvd, ...cvdLight }, dark: false },
  "colour-blind high contrast dark": { t: { ...hcDark, ...cvd }, dark: true },
};
const TINTS = ["markets", "trading", "insights", "account", "admin", "broker", "copilot"];

/** The measured numbers, for the comparison page and the PR (`DM_RATIOS=1 npx vitest run src/ui/darkContrast.test.ts`). */
export function ratios(t: Record<string, number[]>): Record<string, number> {
  const r = (a: string, b: string) => Math.round(contrast(t[a], t[b]) * 100) / 100;
  return {
    "card vs page": r("surface-1", "surface"), "raised vs card": r("surface-2", "surface-1"),
    "border on page": r("border", "surface"), "border on card": r("border", "surface-1"), "border on raised": r("border", "surface-2"),
    "card outline on page": r("border-subtle", "surface"), "card outline on card": r("border-subtle", "surface-1"),
    "muted text / icons on card": r("fg-muted", "surface-1"), "muted on raised": r("fg-muted", "surface-2"),
    "primary button text": r("on-brand", "brand-fill"), "tab underline on page": r("brand", "surface"),
    "link on card": r("brand", "surface-1"),
    "active bottom tab (lowest tint)": Math.min(...TINTS.map((s) => r(`sec-${s}`, "surface-1"))),
  };
}

describe("UI-H-DM: the colour v2 contrast numbers (computed from tokens.css)", () => {
  it.each(Object.entries(THEMES))("%s: DM1 the surfaces step apart", (_, { t, dark }) => {
    expect(contrast(t["surface-1"], t.surface), "card vs page").toBeGreaterThanOrEqual(1.3);
    if (dark) {
      expect(contrast(t["surface-2"], t["surface-1"]), "raised vs card").toBeGreaterThanOrEqual(1.15);
      expect(contrast(t["surface-3"], t["surface-2"]), "pressed vs raised").toBeGreaterThanOrEqual(1.15);
      expect(luminance(t["surface-2"])).toBeGreaterThan(luminance(t["surface-1"]));
    }
  });

  it.each(Object.entries(THEMES))("%s: DM2 every border >= 3 : 1 on the surface it sits on", (_, { t }) => {
    for (const bg of ["surface", "surface-1", "surface-2"]) expect(contrast(t.border, t[bg]), `border on ${bg}`).toBeGreaterThanOrEqual(3);
    for (const bg of ["surface", "surface-1"]) expect(contrast(t["border-subtle"], t[bg]), `card outline on ${bg}`).toBeGreaterThanOrEqual(3);
    // The tab row's active underline (PageTabs: border-brand) on the page and on a card.
    for (const bg of ["surface", "surface-1"]) expect(contrast(t.brand, t[bg]), `tab underline on ${bg}`).toBeGreaterThanOrEqual(3);
  });

  it.each(Object.entries(THEMES))("%s: DM3 icons and labels >= 4.5 : 1, primary + active tab >= 7 : 1", (_, { t }) => {
    for (const bg of ["surface", "surface-1", "surface-2"]) {
      expect(contrast(t["fg-muted"], t[bg]), `muted text / icons / inactive tabs on ${bg}`).toBeGreaterThanOrEqual(4.5);
      expect(contrast(t.fg, t[bg]), `text on ${bg}`).toBeGreaterThanOrEqual(7);
    }
    for (const bg of ["surface", "surface-1"]) expect(contrast(t.brand, t[bg]), `link on ${bg}`).toBeGreaterThanOrEqual(4.5);
    expect(contrast(t["on-brand"], t["brand-fill"]), "primary button / active sub-tab").toBeGreaterThanOrEqual(7);
    expect(contrast(t["on-brand"], t["brand-fill-strong"]), "primary button, pressed").toBeGreaterThanOrEqual(7);
    for (const s of TINTS) {
      expect(contrast(t[`sec-${s}`], t["surface-1"]), `${s}: the active bottom tab (word and icon)`).toBeGreaterThanOrEqual(7);
      expect(contrast(t[`sec-${s}`], t[`sec-${s}-soft`]), `${s}: an inactive tab's icon on its chip`).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("High contrast is never weaker than the normal theme", () => {
    for (const [hc, normal] of [[hcDark, v2Dark], [hcLight, v2Light]]) {
      for (const [fg, bg] of [["border", "surface-1"], ["border-subtle", "surface-1"], ["fg-muted", "surface-1"], ["fg", "surface-1"], ["brand", "surface-1"]]) {
        expect(contrast(hc[fg], hc[bg]), `${fg} on ${bg}`).toBeGreaterThan(contrast(normal[fg], normal[bg]));
      }
    }
  });

  it("the light theme without colour v2 is untouched (v1 values)", () => {
    expect(lightBase.surface).toEqual([244, 246, 249]);
    expect(lightBase.border).toEqual([211, 217, 227]);
    expect(lightBase["brand-fill"]).toEqual([37, 99, 235]);
  });

  it("prints the measured ratios on request", () => {
    if (process.env.DM_RATIOS) console.log(JSON.stringify(Object.fromEntries(Object.entries(THEMES).map(([k, { t }]) => [k, ratios(t)])), null, 1));
    expect(ratios(v2Dark)["card vs page"]).toBeGreaterThanOrEqual(1.3);
  });
});
