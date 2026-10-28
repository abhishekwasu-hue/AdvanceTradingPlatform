import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { LegendCard } from "./LegendCard";
import { cardFor, cardLines, filledSource, isLegends, LINE_KEYS, rangeText, withLegendCards, type LegendsLibrary } from "./legends";
import legendsSample from "./legendsSample.json";
import { ScreenGallery } from "./ScreenGallery";
import taxonomySample from "./taxonomyLegendsSample.json";
import type { Taxonomy } from "./taxonomy";

/** The server's own payloads (backend tests/test_z8_lg1_legends.py and test_z8_tx1_taxonomy.py fail when these samples
 * drift from the data files). */
const lib = legendsSample as unknown as LegendsLibrary;
const tax = taxonomySample as unknown as Taxonomy;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&gt;/g, ">").replace(/&lt;/g, "<").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const ADVICE = [" buy ", " sell ", "recommend", " target", " best ", "guarantee", "endorsed by him", "endorsed by her"];

describe("the Legends sample (U7 LG1)", () => {
  it("is a usable library of eight cards, each filed in the taxonomy under Legends", () => {
    expect(isLegends(lib)).toBe(true);
    for (const bad of [null, {}, { detail: "Not found" }, { header: "x", cards: [{}] }]) expect(isLegends(bad)).toBe(false);
    expect(lib.cards).toHaveLength(8);
    const filed = new Map(tax.screens.map((s) => [s.id, s]));
    for (const c of lib.cards) {
      expect(filed.get(c.id)?.method).toBe("legends");
      expect(filed.get(c.id)?.name).toBe(c.name);
      expect(cardFor(lib, c.id)).toBe(c);
    }
    expect(cardFor(lib, "starter.momentum")).toBeNull();
    expect(cardFor(null, lib.cards[0].id)).toBeNull();
  });

  it("gives every card five lines, each claim line citing a listed source, and the disclaimer naming the person", () => {
    for (const c of lib.cards) {
      const lines = cardLines(c);
      expect(lines.map((l) => l.key)).toEqual([...LINE_KEYS, "source"]);
      expect(lines.length).toBeLessThanOrEqual(5);
      const ids = new Set(c.sources.map((s) => s.id));
      for (const k of LINE_KEYS) expect(c.lines[k].cite.length > 0 && c.lines[k].cite.every((id) => ids.has(id))).toBe(true);
      expect(c.disclaimer).toBe(`Inspired by publicly described methods. Not affiliated with, endorsed by, or representing ${c.legend}. Educational tool, not investment advice.`);
    }
  });

  it("shows the ScreenQL source with the defaults filled in, and ranges in plain numbers", () => {
    const minervini = cardFor(lib, "legends.minervini_trend_template")!;
    expect(minervini.screen.source).toContain("$rs_min");
    expect(filledSource(minervini)).toContain("PercentileRank(PctChange(close, 250)) >= 70");
    expect(filledSource(minervini)).not.toContain("$");
    expect(rangeText(minervini.screen.params[0])).toBe("0.5 to 1");
  });
});

describe("LegendCard (U7 LG1)", () => {
  const card = cardFor(lib, "legends.weinstein_stage2_breakout")!;
  const html = renderToStaticMarkup(<LegendCard card={card} onBack={() => undefined} />);

  it("renders the card as an article named by its heading, with the disclaimer as a note", () => {
    expect(html).toContain(`aria-labelledby="legend-${card.id}"`);
    expect(html).toContain(`id="legend-${card.id}"`);
    expect(html).toContain('role="note"');
    expect(text(html)).toContain(card.disclaimer);
    expect(text(html)).toContain("Back to screens");
  });

  it("lists the five lines as a description list and the sources with their titles", () => {
    expect(html.match(/<dt/g)).toHaveLength(5);
    expect(html.match(/<dd/g)).toHaveLength(5);
    for (const l of cardLines(card)) expect(text(html)).toContain(l.text);
    for (const s of card.sources) expect(text(html)).toContain(s.title);
    expect(html).toContain("<cite");
  });

  it("shows the screen: approximation, timeframe, source and a parameter table with headers", () => {
    expect(text(html)).toContain("An approximation of the published rules.");
    expect(text(html)).toContain(card.approximation);
    expect(html).toContain('aria-label="Screen source"');
    expect(html).toMatch(/<details data-details="legend.source"[^>]*>[\s\S]*Advanced/);     // UI-28 U1.4: code under "Advanced"
    expect(text(html)).toContain(filledSource(card));
    expect(html).toContain("<caption");
    expect(html.match(/scope="col"/g)).toHaveLength(4);
    expect(html.match(/scope="row"/g)).toHaveLength(card.screen.params.length);
  });

  it("keeps Open disabled with its reason until the builder can load the screen (OPEN_QUESTIONS TX1-7)", () => {
    expect(html).toContain(`aria-label="Open ${card.name} in the builder"`);
    expect(html).toMatch(/disabled=""[^>]*title="This screen opens in the builder soon"/);
    expect(html).toContain('data-disabled-reason="true">This screen opens in the builder soon<');   // UI-28 U3.2: the reason as a line
  });

  it("uses no advice wording, no endorsement claim and no Devanagari on any card", () => {
    for (const c of lib.cards) {
      const words = text(renderToStaticMarkup(<LegendCard card={c} />)).toLowerCase();
      for (const w of ADVICE) expect(words).not.toContain(w);
      expect(/[\u0900-\u097f]/.test(words)).toBe(false);
    }
  });
});

describe("ScreenGallery with the Legends library (U7 LG1)", () => {
  const render = (props: Partial<Parameters<typeof ScreenGallery>[0]> = {}) =>
    renderToStaticMarkup(<ScreenGallery taxonomy={tax} canOpen={() => false} onOpen={() => undefined} legends={lib} {...props} />);

  it("shows the library header and a disclaimer and an About button on every Legends card", () => {
    const html = render({ initial: { method: "legends" } });
    expect(html.match(/data-screen-id=/g)).toHaveLength(lib.cards.length);
    expect(text(html)).toContain(lib.header);
    expect(html.match(/data-testid="legend-card-disclaimer"/g)).toHaveLength(lib.cards.length);
    for (const c of lib.cards) expect(html).toContain(`aria-label="About ${c.name}: the method, its sources and the screen"`);
  });

  it("shows no Legends header or About button on screens that are not Legends", () => {
    const html = render({ initial: { method: "indicator" } });
    expect(html).not.toContain('data-testid="legends-header"');
    expect(html).not.toContain("About ");
  });

  it("without the library (its call failed) no Legends screen is listed, so none shows without its disclaimer (L1)", () => {
    const others = tax.screens.filter((s) => s.source !== "legends").length;
    const all = renderToStaticMarkup(<ScreenGallery taxonomy={tax} canOpen={() => false} onOpen={() => undefined} />);
    expect(all).not.toContain('data-screen-id="legends.');
    expect(all.match(/data-screen-id=/g)).toHaveLength(others);
    expect(text(all)).toContain(`${others} screens · All methods → All setups`);
    const folder = renderToStaticMarkup(<ScreenGallery taxonomy={tax} canOpen={() => false} onOpen={() => undefined} initial={{ method: "legends" }} />);
    expect(folder).not.toContain('data-testid="legends-header"');
    expect(folder).not.toContain("legend-card-disclaimer");
    expect(folder).toContain('data-testid="gallery-empty"');
  });

  it("withLegendCards drops Legends screens without a card and recomputes the matrix and total", () => {
    const legendsCount = tax.screens.filter((s) => s.source === "legends").length;
    expect(legendsCount).toBe(lib.cards.length);
    const none = withLegendCards(tax, null);
    expect(none.total).toBe(tax.total - legendsCount);
    expect(none.screens.some((s) => s.source === "legends")).toBe(false);
    expect(Object.values(none.matrix.legends).every((n) => n === 0)).toBe(true);
    expect(Object.keys(none.matrix).sort()).toEqual(Object.keys(tax.matrix).sort());
    expect(none.matrix.indicator).toEqual(tax.matrix.indicator);
    const partial = withLegendCards(tax, { ...lib, cards: lib.cards.slice(1) });
    expect(partial.total).toBe(tax.total - 1);
    expect(partial.screens.some((s) => s.id === lib.cards[0].id)).toBe(false);
    expect(withLegendCards(tax, lib)).toBe(tax);                              // every Legends screen has its card
  });

  it("opens one card in place of the grid", () => {
    const id = "legends.crabel_nr7";
    const html = render({ initial: { method: "legends" }, initialDetail: id });
    expect(html).toContain(`data-legend-id="${id}"`);
    expect(html).not.toContain("data-screen-id=");
    expect(text(html)).toContain(cardFor(lib, id)!.disclaimer);
  });
});
