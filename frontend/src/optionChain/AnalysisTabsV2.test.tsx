import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  TABS, analysesQuery, analysisFor, fetchAnalyses, formatCell, markerPixel, provenance, stateMessage, xPixel, xValues, yScale, yTick,
  type AnalysesResponse, type AnalysisKey,
} from "./analyses";
import analysesSource from "./analyses.ts?raw";
import { DEMO_ANALYSES } from "./analyses.fixture";
import { AnalysisTabs, AnalysisTabsView, MiniChart } from "./AnalysisTabsV2";
import tabsSource from "./AnalysisTabsV2.tsx?raw";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => undefined;
const view = (tab: AnalysisKey, data: AnalysesResponse | null = DEMO_ANALYSES, extra: { error?: string | null; loading?: boolean } = {}) =>
  renderToStaticMarkup(<AnalysisTabsView data={data} error={extra.error ?? null} loading={extra.loading ?? false} tab={tab} onTab={noop} />);
// The platform's advice words (backend app/portfolio/risk_text.py ADVICE_WORDS and app/screener/taxonomy.py SCREENER_WORDS).
const ADVICE = /\b(recommend\w*|advice|advise|should|must|buy|buying|sell|selling|reduce|cut your|add more|hedge now|guarantee\w*|best|safe bet|target\w*|top pick)\b/i;

afterEach(() => { vi.restoreAllMocks(); });

describe("OC2 analyses request", () => {
  it("reads GET /api/option-chain/analyses with the symbol and, when given, the expiry - a read, never a write", async () => {
    expect(analysesQuery(" nifty ")).toBe("symbol=NIFTY");
    expect(analysesQuery("NIFTY", "2026-10-27")).toBe("symbol=NIFTY&expiry=2026-10-27");
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(DEMO_ANALYSES), { status: 200 }));
    const body = await fetchAnalyses("demo", "2026-10-27");
    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toMatch(/\/option-chain\/analyses\?symbol=DEMO&expiry=2026-10-27$/);
    expect(init?.method ?? "GET").toBe("GET");
    expect(body.analyses).toHaveLength(6);
  });
});

describe("OC2 analysis tabs", () => {
  it("shows six tabs in the server's order, one selected", () => {
    expect(TABS.map((t) => t.key)).toEqual(DEMO_ANALYSES.analyses.map((a) => a.key));
    const html = view("support_resistance");
    expect(html.match(/role="tab"/g)).toHaveLength(6);
    expect(html.match(/aria-selected="true"/g)).toHaveLength(1);
    const words = text(html);
    for (const t of TABS) expect(words).toContain(t.label);
    expect(words).toContain("Analyses · DEMO · expiry 2026-10-27");
    expect(words).toContain("Data as of 10:00 IST · 1 earlier snapshot since 09:20 IST");
  });

  it.each(TABS.map((t) => t.key))("the %s tab has its sentences, a small chart and a table", (key) => {
    const html = view(key);
    const analysis = analysisFor(DEMO_ANALYSES, key)!;
    const words = text(html);
    expect(html).toContain(`data-analysis="${key}"`);
    expect(analysis.sentences.length).toBeGreaterThanOrEqual(2);
    expect(analysis.sentences.length).toBeLessThanOrEqual(3);
    for (const s of analysis.sentences) expect(words).toContain(s.replace(/\s+/g, " "));
    expect(html).toMatch(/data-chart="(bar|line)"><svg[^>]*role="img"/);
    expect(html).toContain("<table");
    expect(words).toContain("How it is worked out:");
    // Only the selected tab's panel is drawn.
    for (const other of TABS.filter((t) => t.key !== key)) expect(html).not.toContain(`data-analysis="${other.key}"`);
  });

  it("puts the numbers from the server in the tables, formatted, never invented", () => {
    const pain = text(view("max_pain"));
    // 5 strikes 100-140: value at expiry 100 -> 1500 ... the smallest at 120 (hand-computed in the backend test).
    expect(pain).toContain("Max pain is 120");
    expect(view("max_pain")).toMatch(/bg-surface-2 font-semibold[^>]*>(<td[^>]*>[^<]*<\/td>)*?<td[^>]*>120<\/td>/);
    const build = text(view("build_up"));
    for (const label of ["Long build-up", "Short build-up", "Short covering", "Long unwinding", "No clear change", "Not available"]) expect(build).toContain(label);
    expect(build).toContain("+10");                        // the call OI change at 110
    const pcr = text(view("pcr"));
    expect(pcr).toContain("0.84");
    expect(pcr).toContain("0.99");
  });

  it("describes only: no advice and no buy / sell words anywhere on the tabs", () => {
    for (const t of TABS) expect(text(view(t.key)), t.key).not.toMatch(ADVICE);
    for (const source of [tabsSource, analysesSource]) {
      const strings = source.match(/"[^"\n]*"|`[^`\n]*`/g) ?? [];
      for (const s of strings) expect(s).not.toMatch(ADVICE);
    }
  });
});

describe("OC2 plain states", () => {
  it.each([
    ["no_broker", "Connect your broker and log in to it today to see these analyses. They use your own broker's option chain."],
    ["unavailable", "Your broker did not return the option chain just now. Try again in a moment."],
    ["no_data", "Your broker returned no strikes for this symbol and expiry."],
  ] as const)("%s shows the server's sentence and no numbers", (state, message) => {
    const data: AnalysesResponse = { symbol: "DEMO", expiry: null, generated_at: "2026-10-21T04:30:00Z", state, message, analyses: [] };
    const html = view("pcr", data);
    expect(text(html)).toContain(message);
    expect(html).not.toContain("role=\"tab\"");
    expect(html).not.toContain("data-chart");
    expect(html).not.toContain("<table");
    expect(stateMessage({ ...data, message: null })).toBeTruthy();
  });

  it("a tab whose analysis has no data shows its sentence only", () => {
    const data: AnalysesResponse = {
      ...DEMO_ANALYSES,
      analyses: DEMO_ANALYSES.analyses.map((a) => a.key !== "build_up" ? a : {
        ...a, state: "no_data" as const, tables: [], chart: null, values: {},
        sentences: ["Build-up compares each strike with an earlier snapshot of this session, and none is stored for this expiry yet."],
      }),
    };
    const html = view("build_up", data);
    expect(text(html)).toContain("none is stored for this expiry yet");
    expect(html).not.toContain("data-chart");
    expect(html).not.toContain("<table");
  });

  it("before the first read: a loading line, or the error", () => {
    expect(text(view("pcr", null, { loading: true }))).toContain("Reading the option chain…");
    expect(text(view("pcr", null, { error: "The analyses could not be read. Try again in a moment." }))).toContain("could not be read");
  });

  it("the mounted component starts with the loading line (the read happens after mount)", () => {
    const load = vi.fn(() => Promise.resolve(DEMO_ANALYSES));
    expect(text(renderToStaticMarkup(<AnalysisTabs symbol="DEMO" load={load} />))).toContain("Reading the option chain…");
    expect(text(renderToStaticMarkup(<AnalysisTabs symbol="  " load={load} />))).toContain("Pick a symbol to see the analyses.");
    expect(load).not.toHaveBeenCalled();                     // a server render never reads; the effect does, after mount
  });

  it("says when the own-broker setting keeps earlier snapshots out", () => {
    const note = "Earlier snapshots of the session are not used while your own-broker data setting is on, so the analyses show the latest reading only.";
    const data = { ...DEMO_ANALYSES, session: { snapshots: 0, since: null, note } };
    expect(text(view("pcr", data))).toContain(note);
    expect(provenance(data)).toBe("Data as of 10:00 IST");
  });
});

describe("OC2 cells and chart geometry", () => {
  it("formats each column kind and shows a dash for a missing value", () => {
    expect(formatCell(22000, "strike")).toBe("22000");
    expect(formatCell(122.5, "strike")).toBe("122.5");
    expect(formatCell(1250000, "oi")).toBe("12,50,000");
    expect(formatCell(-1500, "oi_change")).toBe("−1,500");
    expect(formatCell(30, "oi_change")).toBe("+30");
    expect(formatCell(0, "oi_change")).toBe("0");
    expect(formatCell(0.9876, "ratio")).toBe("0.99");
    expect(formatCell(12.288, "pct")).toBe("12.29%");
    expect(formatCell(5, "iv_diff")).toBe("+5.00");
    expect(formatCell("2026-10-21T03:50:00+00:00", "time")).toBe("09:20");
    expect(formatCell(null, "oi")).toBe("–");
    expect(formatCell(null, "text")).toBe("–");
  });

  it("bars always include zero; a line's range is padded; a spot marker falls between strikes", () => {
    const bars = { type: "bar" as const, x_kind: "strike" as const, x_label: "Strike", y_label: "OI", marker: null,
      series: [{ key: "a", label: "A", tone: "up" as const, points: [{ x: 100, y: 5 }, { x: 110, y: 10 }] }] };
    expect(yScale(bars)).toEqual({ min: 0, max: 10 });
    const line = { ...bars, type: "line" as const, series: [{ ...bars.series[0], points: [{ x: 100, y: 1 }, { x: 110, y: 2 }] }] };
    expect(yScale(line)).toEqual({ min: 0.95, max: 2.05 });
    expect(yScale({ ...bars, series: [{ ...bars.series[0], points: [{ x: 100, y: null }] }] })).toBeNull();
    const xs = xValues(bars);
    expect(xs).toEqual([100, 110]);
    expect(markerPixel(105, xs)).toBeCloseTo((xPixel(0, 2) + xPixel(1, 2)) / 2);
    expect(markerPixel(99, xs)).toBeNull();
    expect(yTick(125000)).toBe("1.3L");
    expect(yTick(-35000)).toBe("−35K");
  });

  it("a missing point breaks the line instead of drawing it as zero", () => {
    const chart = { type: "line" as const, x_kind: "time" as const, x_label: "Time", y_label: "PCR", marker: null,
      series: [{ key: "p", label: "PCR", tone: "brand" as const, points: [
        { x: "2026-10-21T03:50:00Z", y: 0.8 }, { x: "2026-10-21T03:55:00Z", y: null }, { x: "2026-10-21T04:00:00Z", y: 0.9 }, { x: "2026-10-21T04:05:00Z", y: 1.0 }] }] };
    const html = renderToStaticMarkup(<MiniChart chart={chart} title="PCR" />);
    expect(html.match(/<polyline/g)).toHaveLength(1);       // only the run 04:00 - 04:05 (09:30 - 09:35 IST) is a line
    expect(html.match(/<circle/g)).toHaveLength(3);
    expect(text(html)).toContain("09:20");
  });

  it("names the v2 file so the colour lint covers it, and uses token classes for every series", () => {
    // `npm run check:v2-colours` checks every *V2.tsx; the tones map to token classes only.
    expect(tabsSource).not.toMatch(/#[0-9a-f]{3,8}\b|rgba?\((?!var)/i);
    expect(analysesSource).toMatch(/stroke-up/);
  });
});
