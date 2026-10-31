import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { StrategyInfo } from "../types";
import { LibraryMainChips, LibraryMoreSettings } from "../ui/LibraryChips";
import { SUGGESTED_NOTE, firstSentence, libraryCardSettings } from "../ui/strategyLabels";
import { LibraryView, libraryShown, type LibraryViewProps } from "./StrategyLibraryV2";

/** M49 U3 (owner-approved mockup docs/ui/U3_LIBRARY.html): the v2 Strategy Library cards. Made-up strategies only. */

const CROSS: StrategyInfo = {
  id: "demo_cross", name: "Made-up average cross", description: "Marks when a fast average crosses a slow one. More words here.",
  category: "indicator_based", timeframes: ["5min"],
  default_params: { tf: "5min", ema_fast: 9, ema_slow: 21, rsi_period: 14, rsi_mid: 50, atr_mult_sl: 1, target_rr: [1.5, 2], min_rr: 1.2 },
};
const TWO: StrategyInfo = {
  id: "demo_two", name: "Made-up two-chart trend", description: "The bigger chart picks the side.", category: "multi_timeframe",
  timeframes: ["5min", "30min"], default_params: { ema_fast: 9, ema_slow: 21, adx_min: 18 },
};
const FEW: StrategyInfo = { id: "demo_few", name: "Made-up few", description: "One line.", category: "indicator_based", timeframes: ["day"],
  default_params: { lookback: 20 } };

const noop = () => undefined;
const view = (p: Partial<LibraryViewProps> = {}) => renderToStaticMarkup(
  <LibraryView strategies={[CROSS, TWO, FEW]} error={null} inUse={new Set(["demo_cross"])} category="all" query="" chosen={null}
    onCategory={noop} onQuery={noop} onChoose={noop} onTry={noop} {...p} />);
/** One card's markup (from its article to the next one). */
const card = (html: string, id: string) => html.split(`data-library-card="${id}"`)[1].split("<article")[0];
const RAW = /ema_fast|ema_slow|rsi_mid|atr_mult_sl|target_rr|min_rr|indicator_based|multi_timeframe|Indicator-based|5min|30min/;

describe("M49 U3: a Library card's settings", () => {
  it("at most three on the card: two settings then the chart; the rest and the suggested stop / targets under More settings", () => {
    const rows = libraryCardSettings(CROSS.default_params, CROSS.timeframes);
    expect(rows.main.map((r) => `${r.label} ${r.value}`)).toEqual(["Fast EMA 9", "Slow EMA 21", "Chart 5-minute"]);
    expect(rows.more.map((r) => r.label)).toEqual(["RSI length", "RSI middle level"]);
    expect(rows.suggested.map((r) => `${r.label} ${r.value}`)).toEqual(["Stop (x ATR) 1", "Targets (R) 1.5 / 2"]);
    expect(JSON.stringify(rows)).not.toContain("R:R");                 // REV-B2: never a minimum R:R
  });

  it("two charts take two of the three places (the bigger first); a strategy with few settings shows only what it has", () => {
    expect(libraryCardSettings(TWO.default_params, TWO.timeframes).main.map((r) => `${r.label} ${r.value}`))
      .toEqual(["Big chart 30-minute", "Small chart 5-minute", "Fast EMA 9"]);
    const few = libraryCardSettings(FEW.default_params, FEW.timeframes);
    expect(few.main.map((r) => `${r.label} ${r.value}`)).toEqual(["Bars looked at 20", "Chart Daily"]);
    expect(few.more).toEqual([]);
    expect(renderToStaticMarkup(<LibraryMoreSettings more={few.more} suggested={few.suggested} />)).toBe("");
  });

  it("every card on the page shows three settings at most, in plain words, never a raw setting name", () => {
    const html = view();
    for (const id of ["demo_cross", "demo_two", "demo_few"]) {
      const main = card(html, id).split("data-main-settings")[1].split("</ul>")[0];
      expect((main.match(/<li /g) ?? []).length).toBeLessThanOrEqual(3);
    }
    expect(html.replace(/data-library-card="[^"]*"/g, "")).not.toMatch(RAW);
  });

  it("More settings (N) is closed and holds the rest, with the suggested group apart and a (?) on a hard word", () => {
    const rows = libraryCardSettings(CROSS.default_params, CROSS.timeframes);
    const html = renderToStaticMarkup(<LibraryMoreSettings more={rows.more} suggested={rows.suggested} />);
    expect(html).toMatch(/^<details data-library-fold="more-settings"/);
    expect(html).not.toMatch(/<details[^>]* open/);
    expect(html).toContain("More settings (4)");
    const [own, suggested] = html.split("data-suggested-exits");
    expect(own).toContain("RSI middle level");
    expect(own).toContain('aria-label="What is RSI?"');
    expect((own.match(/What is RSI\?/g) ?? []).length).toBe(1);        // one (?) per word, not per row
    expect(suggested).toContain(SUGGESTED_NOTE);
    expect(suggested).toContain("Targets (R)");
    expect(renderToStaticMarkup(<LibraryMainChips rows={rows.main} />)).toContain("Fast EMA <b");
  });
});

describe("M49 U3: the page", () => {
  it("the category is the shared Chip in a trader's word; a strategy in use says so", () => {
    const html = view();
    expect(card(html, "demo_few")).toContain('data-chip="category"');
    expect(card(html, "demo_few")).toContain(">Indicators<");
    expect(card(html, "demo_two")).toContain(">Multi-timeframe<");
    expect(card(html, "demo_cross")).toContain('data-chip="in-use"');
    expect(html).toContain("You use now");
    expect(view({ inUse: null })).not.toMatch(/You use/);                // without the bots list: no claim either way
  });

  it("the category filter counts, and a filter with no strategy shows one line with Show all", () => {
    const html = view();
    expect(html).toMatch(/aria-pressed="true"[^>]*>All <span[^>]*>3</);
    expect(html).toMatch(/>Multi-timeframe <span[^>]*>1</);
    expect(html).not.toContain("data-library-empty");
    expect(libraryShown([CROSS, TWO, FEW], "multi_timeframe", "")).toEqual([TWO]);
    expect(libraryShown([CROSS, TWO, FEW], "all", "TWO-CHART")).toEqual([TWO]);
    const empty = view({ query: "nothing like this" });
    expect(empty).toContain("data-library-empty");
    expect(empty).toContain("No strategies here yet.");
    expect(empty).toContain(">Show all</button>");
    expect(empty).not.toContain("data-library-card");
    expect(empty).not.toContain("data-library-detail");
  });

  it("one plain line on the card; the wide-screen panel has the chosen strategy's full text and settings", () => {
    expect(firstSentence(CROSS.description)).toBe("Marks when a fast average crosses a slow one.");
    expect(firstSentence("Touch within 0.5% of it.")).toBe("Touch within 0.5% of it.");
    const html = view({ chosen: "demo_two" });
    expect(card(html, "demo_cross")).not.toContain("More words here.");
    const detail = html.split("data-library-detail")[1];
    expect(detail).toContain("Made-up two-chart trend");
    expect(detail).toContain("Main settings");
    expect(view().split("data-library-detail")[1]).toContain("How it reads the chart");   // the first is chosen by default
  });

  it("is the v2 Library only: the flag-off page is unchanged", async () => {
    const page = (await import("./StrategiesPage.tsx?raw")).default;
    expect(page).toContain("return v2 ? <StrategyLibraryV2 /> : <StrategiesPageV1 />;");
    expect(page).toContain('"Indicator-based"');                       // the flag-off card keeps its words
  });
});
