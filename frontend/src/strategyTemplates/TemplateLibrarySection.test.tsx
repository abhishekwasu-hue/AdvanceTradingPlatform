import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { LibraryView } from "../pages/StrategyLibraryV2";
import type { StrategyTemplate } from "./api";
import { TemplateLibraryView, templateRows } from "./TemplateLibrarySection";

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
const OFF: StrategyTemplate = { ...BASE, id: "B3", name: "Made-up channel", family: "B", family_name: "Breakout", available: false, reads: { long: "A made-up long read.", short: null } };
const noop = () => undefined;
const view = (t: StrategyTemplate[] | null, family = "all") =>
  renderToStaticMarkup(<TemplateLibraryView templates={t} error={null} family={family} onFamily={noop} />);

describe("P2-3: the Templates section", () => {
  it("shows name, family, idea, both reads, the timeframes and the two links", () => {
    const html = view([BASE, OFF]);
    const card = html.split('data-template-card="D2"')[1].split("<article")[0];
    for (const w of ["Made-up crossover", "Trend following", "A made-up one-line idea.", "A signal reads:", "crossed above", "crossed below", "15-minute, Daily"])
      expect(card).toContain(w);
    expect(card).toContain('href="/deployments?template=D2&amp;timeframe=15m"');
    expect(card).toContain('href="/strategies/test?template=D2&amp;timeframe=15m"');
    expect(card).toContain("Use on the trading bot");
    expect(card).toContain("Test it first");
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
});
