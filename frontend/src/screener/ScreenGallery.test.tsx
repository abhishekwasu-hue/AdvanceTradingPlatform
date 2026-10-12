import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ScreenGallery } from "./ScreenGallery";
import sample from "./taxonomySample.json";
import type { Taxonomy } from "./taxonomy";

const tax = sample as unknown as Taxonomy;
const render = (initial = {}, canOpen = () => true) =>
  renderToStaticMarkup(<ScreenGallery taxonomy={tax} canOpen={canOpen} onOpen={() => undefined} initial={initial} />);
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

describe("ScreenGallery (U9 TX1)", () => {
  it("renders the method rail, the setup chips (with counts, zeros included) and a card per filed screen", () => {
    const html = render();
    expect(html).toContain('aria-label="Method"');
    expect(html).toContain('role="group" aria-label="Setup type"');
    for (const m of tax.methods) expect(html).toContain(m.label);
    for (const s of tax.setups) expect(html).toContain(s.label);
    expect(html.match(/data-screen-id=/g)).toHaveLength(tax.screens.length);
    expect(html).toContain("(none)");                                         // empty chips and rail rows say so
    expect(text(html)).toContain(`${tax.total} screens · All methods → All setups`);
    expect(html).toContain('aria-pressed="true"');                           // "All methods" and "All setups"
  });

  it("filters to one cell and shows an empty cell honestly", () => {
    const one = render({ method: "indicator", setup: "momentum" });
    expect(one.match(/data-screen-id=/g)).toHaveLength(1);
    expect(one).toContain('data-screen-id="starter.momentum"');
    const empty = render({ method: "price_action", setup: "breakout" });
    expect(empty).not.toContain("data-screen-id=");
    expect(text(empty)).toContain("No screens are filed under Price action → Breakout / breakdown yet.");
    expect(text(empty)).toContain("0 screens · Price action → Breakout / breakdown");
  });

  it("filters by search", () => {
    const html = render({ query: "EMA" });
    const expected = tax.screens.filter((s) => `${s.name} ${s.definition}`.toLowerCase().includes("ema")).length;
    expect(expected).toBeGreaterThanOrEqual(2);                              // two starters and the Raschke-style card (U7 LG1)
    expect(html.match(/data-screen-id=/g)).toHaveLength(expected);
  });

  it("names each Open button and disables it when the page cannot load the screen", () => {
    const html = render({}, () => false);
    expect(html).toContain('aria-label="Open Momentum in the builder"');
    expect(html.match(/disabled=""/g)?.length).toBe(tax.screens.length);
  });

  it("draws the full method × setup matrix with row and column headers; empty cells read as none", () => {
    const html = render();
    expect(html).toContain("<caption");
    expect(html.match(/scope="row"/g)).toHaveLength(tax.methods.length);
    expect(html.match(/scope="col"/g)).toHaveLength(tax.setups.length + 1);
    const cells = tax.methods.length * tax.setups.length;
    const filled = Object.values(tax.matrix).flatMap((r) => Object.values(r)).filter((n) => n > 0).length;
    expect(html.match(/data-empty-cell/g)).toHaveLength(cells - filled);
  });

  it("uses no advice wording and no Devanagari", () => {
    const words = text(render()).toLowerCase();
    for (const w of [" buy ", " sell ", "recommend", " target", " best ", "guarantee"]) expect(words).not.toContain(w);
    expect(/[ऀ-ॿ]/.test(words)).toBe(false);
  });
});
