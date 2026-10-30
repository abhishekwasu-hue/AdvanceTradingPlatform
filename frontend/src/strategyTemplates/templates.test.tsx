import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { StartBotV2, strategyOptions } from "../paperbot/PaperBotV2";
import pageSource from "../paperbot/PaperBotV2.tsx?raw";
import { TEMPLATE_ID_PREFIX, botName, isTemplateId, templateOf } from "../paperbot/model";
import type { StrategyInfo } from "../types";
import apiSource from "./api.ts?raw";
import type { StrategyTemplate } from "./api";
import { paperBotPathFor, templateLinkFromSearch, typedSettings } from "./link";

/** Strategy templates step 2: templates adopted as paper strategies in the v2 Paper bot form (ui_v2 + strategy_templates). */

const noop = () => undefined;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const intraday: StrategyInfo = { id: "orb_15m_5m", name: "Opening range breakout", description: "Trades the break.", category: "breakout" as StrategyInfo["category"], timeframes: ["5m", "15m"], default_params: {} };
const D2: StrategyTemplate = {
  id: "D2", version: 1, name: "Moving-average crossover", family: "D", family_name: "Trend following",
  idea: "A signal when a fast moving average crosses a slow one on a closed bar.", difficulty: "Beginner", style: "Both",
  timeframes: [{ value: "5m", label: "5-minute" }, { value: "15m", label: "15-minute" }, { value: "1d", label: "Daily" }],
  default_timeframe: "5m", sides_default: "both", params: [], rules: [], available: true,
};

describe("strategy templates: ids and names", () => {
  it("a template strategy id names its template; anything else is not one", () => {
    expect(isTemplateId("template:D2:1:15m:both:,,,,,,")).toBe(true);
    expect(templateOf("template:D2:1:15m:both:,5,,,,,")).toBe("D2");
    expect(templateOf("custom:12")).toBeNull();
    expect(templateOf("orb_15m_5m")).toBeNull();
    expect(TEMPLATE_ID_PREFIX).toBe("template:");
  });

  it("a bot on a template shows the template's name, never the id with its settings", () => {
    const names = new Map([["template:D2", "Moving-average crossover"]]);
    expect(botName({ strategy_id: "template:D2:1:15m:both:,5,,,,," }, names)).toBe("Moving-average crossover");
    expect(botName({ strategy_id: "template:B1:1:15m:both:,,,,," }, new Map())).toBe("Strategy template");
  });
});

describe("strategy templates: the paper bot form", () => {
  const render = (templates: StrategyTemplate[]) => renderToStaticMarkup(<MemoryRouter><StartBotV2 library={[intraday]} custom={[]} hasBroker
    firstField={createRef<HTMLButtonElement>()} onStarted={noop} templates={templates} /></MemoryRouter>);

  it("lists the templates in their own group, only when there are any", () => {
    const html = render([D2]);
    expect(strategyOptions([intraday], [], [], [D2]).filter((r) => r.group === "Strategy templates").map((r) => r.label)).toContain("Moving-average crossover");
    expect(strategyOptions([intraday], [], [], []).some((r) => r.group === "Strategy templates")).toBe(false);
    expect(text(html)).not.toMatch(/template-pick|template:/);
  });

  it("adopting is a pre-fill: the server's strategy id, candles and holding go into the same PAPER request; nothing else starts", () => {
    expect(pageSource).toContain("templatesApi.adopt(pickedId, fromLink ? { timeframe: templateTimeframe, params } : { timeframe: templateTimeframe })");
    expect(pageSource).toContain("timeframe: adoption.deployment.timeframe, holding: adoption.deployment.holding");
    expect(pageSource).toContain('features.on("ui_v2") && features.on("strategy_templates")');
    expect(pageSource).toMatch(/disabled=\{busy \|\| !effectiveId \|\| !!startWhy\}/);   // no Start until the pre-fill is in (REV-H3: and chosen)
    expect(apiSource).toContain('mode: "PAPER"');
    expect(apiSource).not.toContain("/deployments");                              // the template API never creates a bot
  });
});

describe("the template link (idea library slice 2, IDEA-1)", () => {
  it("carries the template, its timeframe and the settings as a pre-fill, and reads them back", () => {
    const path = paperBotPathFor({ template: "D2", timeframe: "15m", params: { fast: 5, use_trend: true, ma_type: "SMA" } });
    expect(path).toBe("/deployments?template=D2&timeframe=15m&set.fast=5&set.use_trend=true&set.ma_type=SMA");
    expect(templateLinkFromSearch(path.slice(path.indexOf("?")))).toEqual(
      { template: "D2", timeframe: "15m", params: { fast: "5", use_trend: "true", ma_type: "SMA" } });
    expect(templateLinkFromSearch("?template=D2")).toEqual({ template: "D2", timeframe: null, params: {} });
  });

  it("drops what is not a template link instead of guessing", () => {
    expect(templateLinkFromSearch("?template=T5")).toBeNull();
    expect(templateLinkFromSearch("?template=<x>")).toBeNull();
    expect(templateLinkFromSearch("")).toBeNull();
    expect(templateLinkFromSearch("?template=D2&timeframe=soon&set.Bad=1&set.fast=")).toEqual({ template: "D2", timeframe: null, params: {} });
  });

  it("types the settings as the template declares them; unknown keys and wrong types are left out", () => {
    const t = { params: [
      { key: "fast", label: "", help: "", type: "int" as const, default: 9 },
      { key: "k", label: "", help: "", type: "float" as const, default: 0.2 },
      { key: "use_trend", label: "", help: "", type: "bool" as const, default: false },
      { key: "ma_type", label: "", help: "", type: "ma_type" as const, default: "EMA" },
    ] };
    expect(typedSettings(t, { fast: "5", k: "0.5", use_trend: "true", ma_type: "SMA", other: "1" }))
      .toEqual({ fast: 5, k: 0.5, use_trend: true, ma_type: "SMA" });
    expect(typedSettings(t, { fast: "5.5", k: "x", use_trend: "yes" })).toEqual({});
  });

  it("the form reads the link's timeframe and settings and says the idea's settings are the start", () => {
    expect(pageSource).toContain("templateLinkFromSearch(window.location.search)");
    expect(pageSource).toContain("typedSettings(picked, initialTemplate.params)");
    expect(pageSource).toContain("Starts with the settings from the idea.");
  });
});
