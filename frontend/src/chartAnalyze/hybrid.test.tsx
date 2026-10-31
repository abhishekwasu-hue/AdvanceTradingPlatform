import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { DISCLAIMER, fixtureHybrid } from "./fixtures";
import HybridResultV2, { areaNote, trendText } from "./HybridResultV2";
import { WITHHELD } from "./signalLanguage";

/** M59 CR-4: the "Advanced analysis" result and the mode picker. Made-up data only (fixtures.ts). */
const noop = () => undefined;
const render = (withAi = true, over: Partial<ReturnType<typeof fixtureHybrid>> = {}) =>
  renderToStaticMarkup(<HybridResultV2 result={{ ...fixtureHybrid(withAi), ...over }} userImageSrc="blob:yours" readingSrc="blob:atp"
    onDownload={noop} onDelete={noop} />);

describe("Advanced analysis: the result", () => {
  it("shows your chart beside ATP's reading of the picked symbol and timeframe, and says why", () => {
    const html = render();
    expect(html).toContain("Your chart");
    expect(html).toContain("ATP&#x27;s reading of DEMO 1-hour");
    expect(html).toContain('src="blob:yours"');
    expect(html).toContain('src="blob:atp"');
    expect(html).toContain("nothing is drawn on it");
    expect(html).toContain("Advanced analysis (code + AI)");
    expect(html).toContain("Area of interest above");
    expect(html).toContain("Trigger candle");
    expect(html).toContain(DISCLAIMER);
  });

  it("the structure table (CR-1 card style), data not ready as a chip, and the areas with points away", () => {
    const html = render();
    expect(html).toMatch(/Timeframe<\/th>.*Trend<\/th>.*Protected level<\/th>.*Last break<\/th>/s);
    expect(html).toContain('data-not-ready="1w"');
    expect(html).toContain("Weekly: data not ready");
    expect(html).toContain("1-hour · your chart");
    expect(html).toContain("101.25");
    expect(html).toContain("Swing break");
    expect(html).toContain("109.10 – 110.40");
    expect(areaNote({ where: "above", low: 1, high: 2, distance: 2.9, state: "fresh", timeframe: null })).toBe("2.90 points away · fresh");
    expect(trendText({ tf: "1h", label: "1-hour", ready: true, trend: "up", weak: true, protected: null, last_break: null })).toBe("Up (weak)");
  });

  it("the checklist marks present and absent facts, never a score", () => {
    const html = render();
    expect(html).toContain('data-present="true"');
    expect(html).toContain('data-present="false"');
    expect(html).not.toMatch(/score|%/i);
  });

  it("with the engine's AI part: its reading; without it: code only and no AI wording", () => {
    expect(render(true)).toContain("AI reading of these facts");
    const code = render(false);
    expect(code).toContain("Advanced analysis (code)");
    expect(code).not.toMatch(/\bAI\b/);
    expect(code).toContain('data-method="code"');
  });

  it("never an order button, a buy / sell call, a target or a stop", () => {
    const html = render();
    const buttons = [...html.matchAll(/<button[^>]*>(.*?)<\/button>/g)].map((m) => m[1].replace(/<[^>]+>/g, ""));
    expect(buttons).toEqual(["Download image", "Delete"]);
    const body = html.replace(DISCLAIMER, "");
    expect(body).not.toMatch(/\b(buy|sell|target|stop|order)\b/i);
    const bad = fixtureHybrid(true);
    bad.ai!.story = ["A made-up line saying buy above the made-up area."];
    expect(renderToStaticMarkup(<HybridResultV2 result={bad} userImageSrc={null} readingSrc={null} onDownload={noop} onDelete={noop} />)).toContain(WITHHELD);
  });
});

describe("Advanced analysis: the mode picker", () => {
  it("is offered next to AI only; while its flag is off it is disabled", async () => {
    vi.resetModules();
    vi.doMock("../ui/useFeatures", () => ({ useFeatures: () => ({ loaded: true, on: (f: string) => f === "chart_analyze" }) }));
    vi.doMock("../ui/PageIntro", () => ({ PageIntro: () => null }));
    const { default: Page, MODE_LABELS } = await import("./ChartAnalyzePageV2");
    const html = renderToStaticMarkup(<MemoryRouter><Page /></MemoryRouter>);
    expect(MODE_LABELS).toEqual({ image_only: "AI only", hybrid: "Advanced analysis (code + AI)" });
    expect(html).toMatch(/data-testid="mode-hybrid"[^>]*aria-disabled="true"|aria-disabled="true"[^>]*data-testid="mode-hybrid"/);
    expect(html).not.toContain('data-testid="hybrid-pick"');                         // no symbol / timeframe inputs while it is off
    expect(html).not.toContain("chart_analyze_hybrid");                              // a user never reads a flag name
    vi.doUnmock("../ui/useFeatures");
    vi.doUnmock("../ui/PageIntro");
  });
});
