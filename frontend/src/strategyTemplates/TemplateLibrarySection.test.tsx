import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { LibraryView } from "../pages/StrategyLibraryV2";
import { REFERENCE_LABEL, showsReferenceLabel, type StrategyTemplate } from "./api";
import { TemplateCard, TemplateLibraryView, templateRows } from "./TemplateLibrarySection";
import { PICK_FIRST, PICK_HINT, PICK_LABEL, TemplateTimeframeField, chipOrder, offeredPick, readsFor, templateLinksFor } from "./TimeframePick";
import { readFileSync } from "node:fs";

/** P2-3: the Templates section. Made-up templates only. */
const TF = [{ value: "15m", label: "15-minute" }, { value: "1d", label: "Daily" }];
const BASE: StrategyTemplate = {
  id: "D2", version: 1, name: "Made-up crossover", family: "D", family_name: "Trend following", idea: "A made-up one-line idea.",
  difficulty: "Beginner", style: "Both", timeframes: TF, default_timeframe: "15m", sides_default: "both",
  params: [{ key: "fast", label: "Fast average", help: "", type: "int", default: 9, min: 2, max: 200, step: 1 },
    { key: "ma_type", label: "Average kind", help: "", type: "enum", default: "EMA", choices: [{ value: "EMA", label: "Exponential" }] }],
  rules: ["The fast average must have fewer bars than the slow one."], available: true,
  reads: { long: "The fast made-up line crossed above the slow one.", short: "The fast made-up line crossed below the slow one." },
};
/** DEF-0: a made-up card as the server now sends it - the neutral sentence and one per timeframe. */
const NAMED: StrategyTemplate = { ...BASE,
  reads: { long: "The fast line crossed above the slow one on the chart you choose.", short: "The fast line crossed below the slow one on the chart you choose." },
  reads_by_timeframe: {
    "15m": { long: "The fast line crossed above the slow one on the 15-minute chart.", short: "The fast line crossed below the slow one on the 15-minute chart." },
    "1d": { long: "The fast line crossed above the slow one on the Daily chart.", short: "The fast line crossed below the slow one on the Daily chart." } } };
const OFF: StrategyTemplate ={ ...BASE, id: "B3", name: "Made-up channel", family: "B", family_name: "Breakout", available: false, reads: { long: "A made-up long read.", short: null } };
const noop = () => undefined;
const view = (t: StrategyTemplate[] | null, family = "all", intradayPackOn = false) =>
  renderToStaticMarkup(<TemplateLibraryView templates={t} error={null} family={family} onFamily={noop} intradayPackOn={intradayPackOn} />);
/** M94: a made-up family H "Intraday" template whose defaults are reference values. */
const INTRADAY: StrategyTemplate = { ...BASE, id: "H6", name: "Made-up opening range", family: "H", family_name: "Intraday",
  style: "Intraday (session-based)", timeframes: [{ value: "5m", label: "5-minute" }], default_timeframe: "5m", reference_values: true };

describe("P2-3: the Templates section", () => {
  it("shows name, family, idea, both reads, the timeframes as chips (none picked) and the two buttons waiting for his pick", () => {
    const html = view([BASE, OFF]);
    const card = html.split('data-template-card="D2"')[1].split("<article")[0];
    for (const w of ["Made-up crossover", "Trend following", "A made-up one-line idea.", "A signal reads:", "crossed above", "crossed below", PICK_LABEL])
      expect(card).toContain(w);
    // DEF-0: the timeframes are chips, none selected; no default timeframe goes anywhere
    const chips = card.split("data-template-timeframe-chips")[1].split("</div>")[0];
    expect(chips.match(/aria-pressed="false"/g)?.length).toBe(2);
    expect(chips).not.toContain('aria-pressed="true"');
    expect(chips).toContain(">15-minute<");
    expect(chips).toContain(">Daily<");
    expect(card).not.toContain("href=");
    expect(card).toContain(PICK_HINT);
    expect(card.match(/<button type="button" disabled=""[^>]*data-(use|test)-template="D2"/g)?.length).toBe(2);
    expect(card).toContain("Use on the trading bot");
    expect(card).toContain("Test it first");
  });

  it("DEF-0: his pick is selected, named in the sentence and carried by both links", () => {
    const html = renderToStaticMarkup(<TemplateCard t={NAMED} reference={false} how={false} initialPick="1d" />);
    expect(html).toContain('aria-pressed="true"');
    expect(html.split('aria-pressed="true"')[1]).toMatch(/^[^>]*>Daily</);
    expect(html).toContain("crossed above the slow one on the Daily chart.");
    expect(html).not.toContain("the chart you choose");
    expect(html).toContain('href="/deployments?template=D2&amp;timeframe=1d"');
    expect(html).toContain('href="/strategies/test?template=D2&amp;timeframe=1d"');
    expect(html).not.toContain(PICK_HINT);
    expect(html).not.toContain("disabled=\"\"");
    // without a pick: the neutral sentence
    const none = renderToStaticMarkup(<TemplateCard t={NAMED} reference={false} how={false} />);
    expect(none).toContain("crossed above the slow one on the chart you choose.");
    expect(none).not.toContain("Daily chart");
  });

  it("DEF-0: the forms' timeframe field (trading bot form, Strategies › Test) starts empty and asks; his pick is selected", () => {
    const none = renderToStaticMarkup(<TemplateTimeframeField t={NAMED} picked={null} onPick={noop} />);
    expect(none).toContain(PICK_LABEL);
    expect(none).toContain('data-chip-value=""');
    expect(none).not.toContain('aria-pressed="true"');
    expect(none).toContain(PICK_FIRST);
    expect(none).toContain('data-value="15m"');
    expect(none).toMatch(/role="group" aria-labelledby="[^"]+" aria-describedby="[^"]+"/);
    const quiet = renderToStaticMarkup(<TemplateTimeframeField t={NAMED} picked={null} onPick={noop} hint={false} />);
    expect(quiet).not.toContain(PICK_FIRST);                                       // the form says it beside Start instead
    const picked = renderToStaticMarkup(<TemplateTimeframeField t={NAMED} picked="1d" onPick={noop} />);
    expect(picked).toContain('data-chip-value="1d"');
    expect(picked.split('aria-pressed="true"')[1]).toMatch(/^[^>]*>Daily</);
    expect(picked).not.toContain(PICK_FIRST);
    expect(renderToStaticMarkup(<TemplateTimeframeField t={NAMED} picked="4h" onPick={noop} />)).toContain('data-chip-value=""');   // not offered
    expect(readFileSync("src/paperbot/PaperBotV2.tsx", "utf8")).not.toMatch(/TEMPLATE_TIMEFRAME|default_timeframe/);
    const test = readFileSync("src/pages/BacktestPage.tsx", "utf8");
    expect(test).not.toContain("bt-template-timeframe");                            // no native select for it any more
    expect(test).toContain("<TemplateTimeframeField");
  });

  it("DEF-0: the sentence and the links follow only a timeframe the template offers", () => {
    expect(readsFor(NAMED, null)).toEqual(["The fast line crossed above the slow one on the chart you choose.", "The fast line crossed below the slow one on the chart you choose."]);
    expect(readsFor(NAMED, "15m")).toEqual(["The fast line crossed above the slow one on the 15-minute chart.", "The fast line crossed below the slow one on the 15-minute chart."]);
    expect(readsFor(NAMED, "4h")).toEqual(readsFor(NAMED, null));                  // not offered: neutral
    expect(readsFor({ ...NAMED, reads_by_timeframe: { "15m": { long: null, short: null } } }, "15m")).toEqual(readsFor(NAMED, null));
    expect(readsFor(BASE, "15m")).toEqual([BASE.reads!.long, BASE.reads!.short]);  // an older server: no per-timeframe sentences
    expect(offeredPick(NAMED, "4h")).toBeNull();
    expect(offeredPick(NAMED, "1d")).toBe("1d");
    expect(templateLinksFor(NAMED, null)).toBeNull();
    expect(templateLinksFor(NAMED, "4h")).toBeNull();
    expect(chipOrder([{ value: "5m" }, { value: "1d" }, { value: "15m" }, { value: "3m" }, { value: "1h" }]).map((x) => x.value))
      .toEqual(["3m", "5m", "15m", "1h", "1d"]);                                  // shortest first: no order hints at a choice
    expect(templateLinksFor(NAMED, "15m")).toEqual({ use: "/deployments?template=D2&timeframe=15m", test: "/strategies/test?template=D2&timeframe=15m" });
  });

  it("a template that is not switched on has the line and no links", () => {
    const card = view([BASE, OFF]).split('data-template-card="B3"')[1];
    expect(card).toContain("Not switched on for your account yet");
    expect(card).not.toContain("href=");
    expect(card).toContain("A made-up long read.");
    expect(card).not.toContain("crossed below");
  });

  it("the family chips filter the list; settings and rules sit behind a fold", () => {
    const html = view([BASE, OFF], "B");
    expect(html).toContain('data-template-card="B3"');
    expect(html).not.toContain('data-template-card="D2"');
    expect(html).toContain('aria-label="Template family"');
    expect(html).toContain("Settings and rules");
    expect(templateRows(BASE).map((r) => `${r.label} ${r.value}`)).toEqual(["Fast average 9", "Average kind Exponential"]);
  });

  it("the page has no Templates section while the flag part is not passed", () => {
    const base = { strategies: [], error: null, inUse: null, category: "all", query: "", chosen: null, onCategory: noop, onQuery: noop, onChoose: noop, onTry: noop };
    expect(renderToStaticMarkup(<LibraryView {...base} />)).not.toContain("data-templates-section");
    const html = renderToStaticMarkup(<LibraryView {...base} after={<TemplateLibraryView templates={[BASE]} error={null} family="all" onFamily={noop} />} />);
    expect(html).toContain("data-templates-section");
    expect(html.indexOf("data-templates-section")).toBeGreaterThan(html.indexOf("data-library-stat"));
  });

  it("M94: the Intraday family chip lists only the Intraday templates", () => {
    const all = view([BASE, OFF, INTRADAY], "all", true);
    expect(all).toContain(">Intraday<");
    const html = view([BASE, OFF, INTRADAY], "H", true);
    expect(html).toContain('data-template-card="H6"');
    expect(html).not.toContain('data-template-card="D2"');
    expect(html).not.toContain('data-template-card="B3"');
    expect(view([BASE, OFF], "all", true)).not.toContain(">Intraday<");
  });

  it("M94: the reference-values label shows on such a template only while intraday_pack is on", () => {
    expect(REFERENCE_LABEL).toBe("Reference values - edit before use");
    const card = view([BASE, INTRADAY], "all", true).split('data-template-card="H6"')[1];
    expect(card).toContain("data-template-reference");
    expect(card).toContain(REFERENCE_LABEL);
    expect(view([BASE, INTRADAY], "all", false)).not.toContain(REFERENCE_LABEL);
    expect(view([BASE], "all", true)).not.toContain(REFERENCE_LABEL);
    expect(showsReferenceLabel(INTRADAY, true)).toBe(true);
    expect(showsReferenceLabel(INTRADAY, false)).toBe(false);
    expect(showsReferenceLabel(BASE, true)).toBe(false);
    expect(showsReferenceLabel({ reference_values: undefined }, true)).toBe(false);
  });
});
