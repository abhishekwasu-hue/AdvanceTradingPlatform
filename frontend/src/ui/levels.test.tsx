import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import css from "../styles/tokens.css?raw";
import { LevelChip } from "./LevelChip";
import { BUDGET_NEAR, budgetLevel, CHIP_TONES, LEVELS, TREND, vixLevel } from "./levels";
import { TrendChip } from "./TrendChip";

/** UX-11b C6: TrendChip / LevelChip from the one band table, and an AA contrast check of every chip tone on every surface. */

describe("the shared band tables", () => {
  it("VIX bands use the backend's edges (market_memory.VIX_BANDS 12 / 16 / 20)", () => {
    expect([11.9, 12, 15.99, 16, 19.99, 20, 35].map(vixLevel)).toEqual(["LOW", "NORMAL", "NORMAL", "ELEVATED", "ELEVATED", "HIGH", "HIGH"]);
  });
  it("a budget is near its limit from 80 % and reached at 100 %", () => {
    expect(BUDGET_NEAR).toBe(0.8);
    expect([budgetLevel(0, 1000), budgetLevel(799, 1000), budgetLevel(800, 1000), budgetLevel(1000, 1000), budgetLevel(5, 0)])
      .toEqual(["INSIDE", "INSIDE", "NEAR", "REACHED", null]);
  });
  it("colour follows the owner's table and always comes with a mark", () => {
    expect([TREND.UP.tone, TREND.DOWN.tone, TREND.SIDEWAYS.tone]).toEqual(["up", "down", "neutral"]);
    expect([LEVELS.vix.LOW.tone, LEVELS.vix.NORMAL.tone, LEVELS.vix.ELEVATED.tone, LEVELS.vix.HIGH.tone]).toEqual(["up", "neutral", "warn", "down"]);
    expect([LEVELS.mood.POSITIVE.tone, LEVELS.mood.NEGATIVE.tone, LEVELS.mood.MIXED.tone]).toEqual(["up", "down", "neutral"]);
    expect([LEVELS.budget.INSIDE.tone, LEVELS.budget.NEAR.tone, LEVELS.budget.REACHED.tone]).toEqual(["up", "warn", "down"]);
    for (const m of [...Object.values(TREND), ...Object.values(LEVELS).flatMap((s) => Object.values(s))]) expect(m.mark).toBeTruthy();
  });
});

describe("TrendChip and LevelChip", () => {
  it("render the word with its mark; colour never alone", () => {
    const html = (el: JSX.Element) => renderToStaticMarkup(el);
    const up = html(<TrendChip trend="UP" />);
    expect(up).toContain('data-chip="trend-up"');
    expect(up).toContain("text-up");
    expect(up.replace(/<[^>]+>/g, "")).toBe("▲Up");
    expect(html(<TrendChip trend="DOWN" />)).toContain("text-down");
    expect(html(<TrendChip trend={null} />).replace(/<[^>]+>/g, "")).toBe("◆Sideways");
    expect(html(<LevelChip scale="vix" level="ELEVATED" />)).toContain("text-warn");
    expect(html(<LevelChip scale="mood" level="MIXED" />)).toContain("text-fg-muted");
    expect(html(<LevelChip scale="budget" level="NEAR" />).replace(/<[^>]+>/g, "")).toBe("!Near the limit");
    expect(html(<LevelChip scale="vix" level={null} />)).toBe("");
  });
});

// --- AA contrast (WCAG 2.x) of every chip tone's words on its own tint, over every surface token ---------------------------
function block(selector: string): Record<string, number[]> {
  const re = new RegExp(`(?:^|\\n)${selector.replace(/[[\]"=()]/g, (c) => `\\${c}`)}\\s*\\{([^}]*)\\}`, "g");
  const out: Record<string, number[]> = {};
  for (const m of css.matchAll(re)) for (const [, name, value] of m[1].matchAll(/--([\w-]+):\s*([\d\s]+);/g)) out[name] = value.trim().split(/\s+/).map(Number);
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
const cvd = block(':root[data-cvd="on"]');
const cvdLight = block(':root[data-theme="light"][data-cvd="on"]');
const THEMES: Record<string, Record<string, number[]>> = {
  "dark": base, "light": lightBase, "v2 dark": v2Dark, "v2 light": v2Light,
  "colour-blind dark": { ...v2Dark, ...cvd }, "colour-blind light": { ...v2Light, ...cvd, ...cvdLight },
};
const SURFACES = ["surface", "surface-1", "surface-2"];

/** The meaning chip's words and background (src/ui/Chip.tsx PILL_MEANING: opaque tint tokens) - and, for the neutral
 * chip, the surface itself where its fill matches it. */
function chipColours(t: Record<string, number[]>, tone: string, surface: string): [number[], number[]] {
  void surface;                                       // opaque fills: the same ratio on every surface
  return tone === "neutral" ? [t["fg-muted"], t["surface-2"]] : [t[tone], t[`${tone}-tint`]];
}

/** A signed number's words straight on a surface (Signed: no fill). */
function textOn(t: Record<string, number[]>, tone: string, surface: string): number {
  return Math.round(contrast(t[tone], t[surface]) * 100) / 100;
}

export function chipRatios(t: Record<string, number[]>): Record<string, number> {
  const out: Record<string, number> = {};
  for (const tone of CHIP_TONES) for (const s of SURFACES) {
    const [fg, bg] = chipColours(t, tone, s);
    out[`${tone} on ${s}`] = Math.round(contrast(fg, bg) * 100) / 100;
  }
  return out;
}

describe("UX-11b: every chip tone passes AA on every surface", () => {
  if (process.env.CHIP_RATIOS) it("print", () => {
    for (const [k, t] of Object.entries(THEMES)) {
      console.log(k, JSON.stringify(chipRatios(t)));
      console.log(k, "signed", JSON.stringify(Object.fromEntries(["up", "down"].flatMap((tone) => SURFACES.map((s) => [`${tone} on ${s}`, textOn(t, tone, s)])))));
    }
  });
  it.each(Object.entries(THEMES))("%s", (_, t) => {
    for (const [name, ratio] of Object.entries(chipRatios(t))) expect(ratio, name).toBeGreaterThanOrEqual(4.5);
  });
});

// A signed number (no fill) sits on a card. On the v2 light page and on a raised row the up / down words measure 3.85-4.5
// (CHIP_RATIOS=1 prints them): the colour_v3 palette (UX-14) fixes the tokens themselves; until then numbers stay on cards.
describe("UX-11b: signed numbers pass AA on a card", () => {
  it.each(Object.entries(THEMES))("%s", (_, t) => {
    for (const tone of ["up", "down"]) expect(textOn(t, tone, "surface-1"), `${tone} on a card`).toBeGreaterThanOrEqual(4.5);
  });
});
