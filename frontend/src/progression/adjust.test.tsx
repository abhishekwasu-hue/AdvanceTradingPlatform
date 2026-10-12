import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { AdjustResult, AdjustSide } from "../optionsBuilder/api";
import { AdjustResultView } from "./AdjustDrawer";
import { ADJUST_DEFAULTS, adjustFields, changeLine, compareRows, curvePaths, moneyLine } from "./adjust";
import { tProgression as t } from "./i18n";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&lt;/g, "<").replace(/&amp;/g, "&").replace(/\s+/g, " ");

const side = (over: Partial<AdjustSide>): AdjustSide => ({
  legs: [], at_expiry: [-100, 50, -100], single_expiry: true, breakevens: [21800, 22200], pop: 0.62, pop_basis: "the legs held",
  extremes: { max_profit: 5750, max_loss: null, unbounded_profit: false, unbounded_loss: true },
  greeks: { delta: 0.1, gamma: 0, theta: 120.5, vega: -900.25, rho: 0 }, net_premium: 5750, ...over,
});
// Shaped as tests/test_n2_builder_adjust.py's iron-condor answer.
const R: AdjustResult = {
  kind: "iron_condor", description: "Buy wings at CE 22500 and PE 21500: the short strangle becomes an iron condor", realised: 0, cash: -1500.4,
  prices: [21000, 22000, 23000],
  changes: [{ action: "open", leg: { direction: "BUY", option_type: "CE", strike: 22500, premium: 14.2, lots: 1, lot_size: 50, expiry: "2026-03-16", iv: 0.15 },
              price: 14.2 }],
  after_legs: [], before: side({}),
  after: side({ at_expiry: [-50, 40, -50], extremes: { max_profit: 4250, max_loss: -10750, unbounded_profit: false, unbounded_loss: false }, pop: 0.58 }),
  greeks_change: { delta: 0, gamma: 0, theta: -10, vega: 100, rho: 0 }, placeable: false,
  why_not: "An adjustment is a model on this page; nothing is placed, staged or proposed from here.", disclaimer: "Model estimates.",
};

describe("adjust helpers (N2-7)", () => {
  it("builds each adjustment's fields or refuses unusable ones", () => {
    expect(adjustFields(ADJUST_DEFAULTS)).toEqual({ index: 0, steps: 1 });
    expect(adjustFields({ ...ADJUST_DEFAULTS, steps: "0" })).toBeNull();                         // a roll to nowhere
    expect(adjustFields({ ...ADJUST_DEFAULTS, steps: "0", expiry: "2026-04-30" })).toEqual({ index: 0, steps: 0, expiry: "2026-04-30" });
    expect(adjustFields({ ...ADJUST_DEFAULTS, kind: "add_hedge", width: "3", newIv: "16" })).toEqual({ index: 0, width_steps: 3, new_iv: 0.16 });
    expect(adjustFields({ ...ADJUST_DEFAULTS, kind: "iron_condor", width: "0" })).toBeNull();
    expect(adjustFields({ ...ADJUST_DEFAULTS, kind: "close_side", side: "PE" })).toEqual({ side: "PE" });
    expect(adjustFields({ ...ADJUST_DEFAULTS, steps: "1.5" })).toBeNull();
  });

  it("writes the changes, the money and the comparison rows", () => {
    expect(changeLine(R.changes[0], t)).toBe("Open BUY CE 22,500 (2026-03-16) ×1 at 14.20");
    expect(moneyLine(R, t)).toBe("Locked in by closes: ₹0 · premium today: -₹1,500");
    const rows = compareRows(R, t);
    expect(rows[1]).toEqual(["Max loss", "unbounded", "-₹10,750"]);
    expect(rows[3]).toEqual(["PoP (legs held)", "62.0%", "58.0%"]);
    expect(rows.map((r) => r[0])).toEqual(["Max profit", "Max loss", "Breakevens", "PoP (legs held)", "Net premium", "Delta", "Theta / day", "Vega"]);
    const p = curvePaths(R, 600, 180);
    expect(p.before.startsWith("M0.0,")).toBe(true);
    expect(p.zeroY).not.toBeNull();
  });
});

describe("AdjustResultView (N2-7)", () => {
  it("shows the change, both curves and the table, and says nothing is placed", () => {
    const html = renderToStaticMarkup(<AdjustResultView result={R} onUse={() => undefined} />);
    const words = text(html);
    expect(words).toContain("Buy wings at CE 22500 and PE 21500");
    expect(html.match(/<path /g)?.length).toBe(2);
    expect(words).toContain("Max loss unbounded -₹10,750");
    expect(words).toContain("nothing is placed, staged or proposed from here");
    expect(words).toContain("Use the legs after in the builder");
    expect(html.match(/<button/g)?.length).toBe(1);
  });
});
