import { createRef } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { StartBotV2 } from "../paperbot/PaperBotV2";
import pageSource from "../paperbot/PaperBotV2.tsx?raw";
import { TEMPLATE_ID_PREFIX, botName, isTemplateId, templateOf } from "../paperbot/model";
import type { StrategyInfo } from "../types";
import apiSource from "./api.ts?raw";
import type { StrategyTemplate } from "./api";

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
    firstField={createRef<HTMLSelectElement>()} onStarted={noop} templates={templates} /></MemoryRouter>);

  it("lists the templates in their own group, only when there are any", () => {
    const html = render([D2]);
    expect(html.match(/<optgroup label="Strategy templates">[\s\S]*?<\/optgroup>/)?.[0]).toContain("Moving-average crossover");
    expect(render([])).not.toContain("Strategy templates");
    expect(text(html)).not.toMatch(/template-pick|template:/);
  });

  it("adopting is a pre-fill: the server's strategy id, candles and holding go into the same PAPER request; nothing else starts", () => {
    expect(pageSource).toContain("templatesApi.adopt(pickedId, { timeframe: templateTimeframe })");
    expect(pageSource).toContain("timeframe: adoption.deployment.timeframe, holding: adoption.deployment.holding");
    expect(pageSource).toContain('features.on("ui_v2") && features.on("strategy_templates")');
    expect(pageSource).toMatch(/disabled=\{busy \|\| !effectiveId\}/);           // no Start until the pre-fill is in
    expect(apiSource).toContain('mode: "PAPER"');
    expect(apiSource).not.toContain("/deployments");                              // the template API never creates a bot
  });
});
