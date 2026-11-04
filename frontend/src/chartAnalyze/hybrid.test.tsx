import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { DISCLAIMER, fixtureHybrid, fixtureHybridOld } from "./fixtures";
import HybridResultV2, { areaDistance, checksLine, trendText } from "./HybridResultV2";
import { WITHHELD } from "./signalLanguage";
import type { HybridReading } from "./types";

/** M59 CR-4 / ADV-2: the "Advanced analysis" result and the mode picker. Made-up data only (fixtures.ts). */
const noop = () => undefined;
const render = (withAi = true, over: Partial<ReturnType<typeof fixtureHybrid>> = {}) =>
  renderToStaticMarkup(<HybridResultV2 result={{ ...fixtureHybrid(withAi), ...over }} userImageSrc="blob:yours" readingSrc="blob:atp"
    onDownload={noop} onDelete={noop} />);
const renderOld = (reading: Partial<HybridReading> = {}) => {
  const old = fixtureHybridOld(false);
  return renderToStaticMarkup(<HybridResultV2 result={{ ...old, reading: { ...old.reading, ...reading } }} userImageSrc="blob:yours" readingSrc="blob:atp"
    onDownload={noop} onDelete={noop} />);
};

describe("Advanced analysis: the result", () => {
  it("shows your chart beside ATP's reading of the picked symbol and timeframe; the picture's note is positive, behind Details", () => {
    const html = render();
    expect(html).toContain("Your chart");
    expect(html).toContain("ATP&#x27;s reading of DEMO · 1-hour");
    expect(html).toContain('src="blob:yours"');
    expect(html).toContain('src="blob:atp"');
    expect(html).not.toContain("does not know");
    expect(html).toMatch(/data-details="chart-analyze.reading-picture".*data-testid="side-by-side-note"[^>]*>ATP draws its reading.*Download image/s);
    expect(html).toContain("Advanced analysis (code + AI)");
    expect(html).toContain("Area of interest above");
    expect(html).toContain("Trigger candle");
    expect(html).toContain(DISCLAIMER);
  });

  it("ADV-2: header chips (label + state), close and candles read, the structure facts, higher degree behind (?)", () => {
    const html = render();
    const text = html.replace(/<[^>]+>/g, "");
    expect(html).toMatch(/data-testid="hybrid-state"[^>]*>Up · pullback running</);
    expect(text).toContain("Close 106.20 · 480 1-hour candles read · from ATP&#x27;s stored and your broker&#x27;s candles");
    expect(text).toContain("SwingsLL 99.40→ HH 111.80→ HL 101.25→ HH 108.60");
    expect(text).toContain("Protected low101.25 −4.95 · 4.66% below");
    expect(text).toContain("Last breakSwing break ↑ 104.50");
    expect(html).toContain('aria-label="Help: Higher degree"');
    expect(html).not.toContain("Daily range between");                               // behind (?) until opened
    expect(trendText({ tf: "1h", label: "1-hour", ready: true, trend: "up", weak: true, protected: null, last_break: null })).toBe("Up (weak)");
    expect(trendText({ tf: "1h", label: "1-hour", ready: true, trend: null, weak: false, protected: null, last_break: null })).toBe("");
  });

  it("ADV-2: only ready timeframes in the table; areas with band, tag and %; scenarios; what to watch", () => {
    const html = render();
    const text = html.replace(/<[^>]+>/g, "");
    expect(html).toMatch(/Timeframe<\/th>.*Trend<\/th>.*Protected level<\/th>.*Last break<\/th>/s);
    expect(html).not.toContain('data-tf="1w"');                                       // not ready: left out
    expect(html).not.toContain('data-tf="1h"');                                       // the picked one is the facts above
    expect(html).toContain('data-tf="1d"');
    expect(html).toContain('data-tf="15m"');
    expect(text).toContain("Above109.10 – 110.40fresh2.73%");
    expect(text).toContain("Below102.30 – 103.60tested2.45%");
    expect(areaDistance({ where: "above", low: 1, high: 2, distance: 2.9, state: "fresh", timeframe: null })).toBe("2.90 points");
    expect(areaDistance({ where: "above", low: 1, high: 2, distance: 2.9, state: "fresh", timeframe: null, distance_pct: 2.3 })).toBe("2.30%");
    expect(text).toContain("Main: The made-up pullback reaches");
    expect(text).toContain("Other: A 1-hour close below 101.25");
    expect(text).toMatch(/Price at 102.30 – 103.60handover · commitment/);
    expect(html.match(/data-watch/g)).toHaveLength(3);
  });

  it("the checklist shows the present facts only; the count is behind Details; never a score", () => {
    const html = render();
    expect(html).toContain('data-present="true"');
    expect(html).not.toContain('data-present="false"');
    expect(html).not.toContain("Between areas");
    expect(html).toMatch(/data-details="chart-analyze.checks".*5 of 7 checks present/s);
    const checklist = html.slice(html.indexOf('data-block="hy-checklist"'), html.indexOf('data-block="hy-ai"'));
    expect(checklist).not.toMatch(/score|%/i);
    expect(checksLine([{ present: true }, { present: false }])).toBe("1 of 2 checks present");
  });

  it("no placeholders: never '-', 'Not read yet', 'Not formed yet', 'None', 'data not ready'", () => {
    for (const html of [render(), renderOld(), renderOld({ table: [], areas: [], checklist: [], last_close: null, protected: null, last_break: null })]) {
      const text = html.replace(/<[^>]+>/g, " ");
      expect(text).not.toMatch(/Not read yet|Not formed yet|\bNone\b|data not ready/i);
      expect(html).not.toMatch(/>\s*[-–]\s*</);                                      // no lone dash in a cell
    }
  });

  it("an old stored result (no ADV-2 fields) renders with only the sections it has", () => {
    const html = renderOld();
    const text = html.replace(/<[^>]+>/g, "");
    for (const block of ["hy-structure", "hy-areas", "hy-checklist"]) expect(html).toContain(`data-block="${block}"`);
    for (const block of ["hy-scenarios", "hy-watch"]) expect(html).not.toContain(`data-block="${block}"`);
    expect(html).not.toContain('data-testid="hybrid-state"');
    expect(html).not.toContain("Help: Higher degree");
    expect(text).toContain("Close 106.20 · from the stored candles");
    expect(text).toContain("TrendUp (weak)");                                          // from the picked timeframe's row
    expect(text).toContain("Protected low101.25 −4.95 · 4.66% below");
    expect(text).toContain("Above109.10 – 110.40fresh2.90 points");
    const empty = renderOld({ table: [], areas: [], checklist: [], last_close: null, protected: null, last_break: null });
    for (const block of ["hy-structure", "hy-areas", "hy-checklist", "hy-scenarios", "hy-watch"]) expect(empty).not.toContain(`data-block="${block}"`);
    expect(empty).toContain(DISCLAIMER);
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
    const buttons = [...html.matchAll(/<button[^>]*>(.*?)<\/button>/g)].map((m) => m[1].replace(/<[^>]+>/g, "")).filter(Boolean);       // the (?) icon button has no text
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

describe("M84: the symbol and timeframe are read from the chart", () => {
  const TF = [{ id: "15m", label: "15-minute" }, { id: "1h", label: "1-hour" }];

  it("says what was read from the chart, and names a value the user typed as his", async () => {
    const { readLine } = await import("./ChartReadV2");
    expect(readLine({ symbol: "NIFTY 50", timeframe: "15m" }, { symbol: "chart", timeframe: "chart" })).toBe("Read from your chart: NIFTY 50 · 15m");
    expect(readLine({ symbol: "DEMO", timeframe: "1h" }, { symbol: "you", timeframe: "you" })).toBe("Your symbol and timeframe: DEMO · 1h");
    expect(readLine({ symbol: "DEMO", timeframe: "15m" }, { symbol: "you", timeframe: "chart" })).toBe("DEMO (yours) · 15m (read from your chart)");
  });

  it("the result shows the read values with Change in place; no order button", async () => {
    const { ChartReadLine } = await import("./ChartReadV2");
    const result = { ...fixtureHybrid(true), read_from_chart: { symbol: "DEMO", timeframe: "1h", symbol_text: "DEMO", timeframe_text: "60" },
      pick_source: { symbol: "chart" as const, timeframe: "chart" as const } };
    const html = renderToStaticMarkup(<HybridResultV2 result={result} userImageSrc={null} readingSrc={null} onDownload={noop} onDelete={noop}
      readLine={<ChartReadLine result={result} timeframes={TF} running={false} onRun={noop} />} />);
    expect(html).toContain("Read from your chart: DEMO · 1h");
    const buttons = [...html.matchAll(/<button[^>]*>(.*?)<\/button>/g)].map((m) => m[1].replace(/<[^>]+>/g, "")).filter(Boolean);       // the (?) icon button has no text
    expect(buttons).toEqual(["Change", "Download image", "Delete"]);
  });

  it("a value that could not be read: its exact reason, and only that field is asked for", async () => {
    const { NeedsCard } = await import("./ChartReadV2");
    const html = renderToStaticMarkup(<NeedsCard timeframes={TF} running={false} onRun={noop}
      read={{ symbol: null, timeframe: "15m", symbol_text: "Made-up Holdings", timeframe_text: "15" }}
      needs={[{ field: "symbol", reason: "“Made-up Holdings” from your chart is not in ATP's instrument list.", ask: "Type the chart's symbol to continue." }]} />);
    expect(html).toContain("Type the chart&#x27;s symbol to continue.");
    expect(html).toContain("not in ATP&#x27;s instrument list");
    expect(html).toContain("Read from your chart: 15m");
    expect(html).toContain("The chart&#x27;s symbol");
    expect(html).not.toContain("The chart&#x27;s timeframe");
    const tfOnly = renderToStaticMarkup(<NeedsCard timeframes={TF} running={false} onRun={noop} read={null}
      needs={[{ field: "timeframe", reason: "The timeframe could not be read from your chart.", ask: "Pick the chart's timeframe to continue." }]} />);
    expect(tfOnly).toContain("The chart&#x27;s timeframe");
    expect(tfOnly).not.toContain("The chart&#x27;s symbol");
  });
});
