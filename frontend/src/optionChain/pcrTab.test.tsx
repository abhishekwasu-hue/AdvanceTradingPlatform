import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { AnalysisPanel, MiniChart } from "./AnalysisTabsV2";
import { type Analysis, type AnalysisChart, formatCell, pcrHeadline, pointNote, timeTicks, visibleChart } from "./analyses";
import { VERDICT_WORDS } from "./oiTab";

/** M61 PCR-1 / PCR-2 / PCR-3 / PCR-4: the PCR tab, on a made-up session path (round invented numbers). */
const t = (hhmm: string) => `2026-10-30T${hhmm}:00+05:30`;
const POINTS = [
  { x: t("06:45"), y: 1.15, phase: "pre_open" as const, hollow: false, put_oi: 52_00_000, call_oi: 45_20_000, atm: 22_950 },
  { x: t("09:15"), y: 1.0, phase: "session" as const, hollow: false, put_oi: 46_00_000, call_oi: 46_00_000, atm: 23_000 },
  { x: t("10:40"), y: 1.05, phase: "session" as const, hollow: false, put_oi: 48_30_000, call_oi: 46_00_000, atm: 23_000 },
  { x: t("11:50"), y: 0.82, phase: "session" as const, hollow: true, put_oi: 37_70_000, call_oi: 46_00_000, atm: 23_000 },
  { x: t("12:30"), y: 1.03, phase: "session" as const, hollow: false, put_oi: 49_44_000, call_oi: 48_00_000, atm: 23_050 },
  { x: t("22:44"), y: 1.03, phase: "after_close" as const, hollow: false, put_oi: 49_44_000, call_oi: 48_00_000, atm: 23_050 },
];
const CHART: AnalysisChart = { type: "line", x_kind: "time", x_label: "Time", y_label: "PCR", marker: null,
  series: [{ key: "pcr_near", label: "Near-ATM PCR", tone: "brand", points: POINTS }], x_marks: [{ x: t("12:30"), label: "ATM 23,050" }] };
const PCR: Analysis = {
  key: "pcr", title: "PCR", state: "ok", method: "PCR is total put OI divided by total call OI.",
  tables: [{ title: "Now", help: "Near ATM: the ATM strike (the listed strike nearest the spot) and the 5 listed strikes on each side of it - here 11 strikes from 22,800 to 23,300 around ATM 23,050.",
    columns: [{ key: "scope", label: "Strikes", kind: "text" }, { key: "put_oi", label: "Put OI", kind: "oi" }, { key: "call_oi", label: "Call OI", kind: "oi" },
      { key: "pcr", label: "PCR", kind: "ratio" }],
    rows: [{ scope: "Overall (75 strikes)", put_oi: 23_04_40_340, call_oi: 14_40_25_000, pcr: 1.6 },
      { scope: "Near ATM (11 strikes 22,800-23,300)", put_oi: 49_44_000, call_oi: 48_00_000, pcr: 1.03 }] }],
  chart: CHART,
  sentences: ["Overall PCR 1.60 | Near-ATM PCR 1.03 (11 strikes 22,800-23,300)", "Since 09:15 IST the near-ATM PCR moved from 1.00 to 1.03.",
    "Left out of the comparison: 2 readings outside the session and 1 partial reading."],
  values: { overall: 1.6, near_atm: 1.03, atm: 23_050, window: [22_800, 22_850, 22_900, 22_950, 23_000, 23_050, 23_100, 23_150, 23_200, 23_250, 23_300] },
};
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/\s+/g, " ");

describe("M61 PCR-3: numbers first", () => {
  it("heads the tab with the two ratios and the near-ATM band", () => {
    expect(pcrHeadline(PCR)).toBe("Overall PCR 1.60 · Near-ATM PCR 1.03 (11 strikes 22,800–23,300)");
    const html = text(renderToStaticMarkup(<AnalysisPanel analysis={PCR} />));
    expect(html).toContain("Overall PCR 1.60 · Near-ATM PCR 1.03 (11 strikes 22,800–23,300)");
    expect(html).not.toContain("Overall PCR 1.60 |");
  });
  it("shows the Now table in lakh / crore and the near-ATM definition behind (?)", () => {
    expect(formatCell(23_04_40_340, "oi")).toBe("23.04 Cr");
    const html = renderToStaticMarkup(<AnalysisPanel analysis={PCR} />);
    expect(text(html)).toContain("23.04 Cr");
    expect(text(html)).toContain("49.44 L");
    expect(html).toContain('aria-label="Help: Now: the strikes"');
    expect(text(html)).not.toMatch(/\d,\d\d,\d\d,\d{3}/);
  });
});

describe("M61 PCR-1 / PCR-2: the session path", () => {
  it("leaves pre-open and after-close readings off the chart by default; the toggle brings them back", () => {
    expect(visibleChart(CHART, false).series[0].points.map((p) => p.x)).toEqual([t("09:15"), t("10:40"), t("11:50"), t("12:30")]);
    expect(visibleChart(CHART, true).series[0].points).toHaveLength(6);
    const html = renderToStaticMarkup(<AnalysisPanel analysis={PCR} />);
    expect(html).toContain("Show pre-open and after-close readings");
    expect(html).toMatch(/data-show-outside[^>]*|aria-pressed="false"[^>]*data-show-outside/);
    expect(html.match(/<circle[^>]*><title>/g)).toHaveLength(4);                // the chart dots (not the (?) icons)
  });
  it("draws a partial reading hollow and off the line; marks the ATM change; hovers in lakh", () => {
    const html = renderToStaticMarkup(<MiniChart chart={visibleChart(CHART, false)} title="PCR" />);
    expect(html.match(/data-hollow="true"/g)).toHaveLength(1);
    expect(html.match(/<polyline/g)).toHaveLength(1);
    expect((html.match(/<polyline points="([^"]+)"/)?.[1] ?? "").split(" ")).toHaveLength(3);       // 09:15, 10:40, 12:30
    expect(html).toContain("data-x-mark");
    expect(text(html)).toContain("ATM 23,050");
    expect(text(html)).toContain("Hollow dot: partial reading");
    expect(pointNote("Near-ATM PCR", POINTS[4], "time")).toBe("Near-ATM PCR at 12:30: 1.03 · Put OI 49.44 L · Call OI 48 L · ATM 23,050");
    expect(pointNote("Near-ATM PCR", POINTS[3], "time")).toContain("partial reading");
  });
  it("puts a time tick every N minutes, on the clock, never overlapping", () => {
    const ticks = timeTicks(Date.parse(t("09:15")), Date.parse(t("15:30")));
    expect(ticks.length).toBeLessThanOrEqual(6);
    const gaps = ticks.slice(1).map((v, i) => v - ticks[i]);
    expect(new Set(gaps).size).toBe(1);
    expect(gaps[0] % (60 * 60_000)).toBe(0);                                   // 09:15 - 15:30: every 2 hours
    expect(timeTicks(Date.parse(t("09:20")), Date.parse(t("09:35")))).toHaveLength(4);   // 09:20, 09:25, 09:30, 09:35
    const html = renderToStaticMarkup(<MiniChart chart={visibleChart(CHART, false)} title="PCR" />);
    const labels = [...html.matchAll(/<text[^>]*>(\d\d:\d\d)<\/text>/g)].map((m) => m[1]);
    expect(labels.length).toBeGreaterThan(1);
    expect(new Set(labels).size).toBe(labels.length);
  });
});

describe("M61 PCR-4: information only", () => {
  it("no verdict words anywhere on the PCR tab", () => {
    expect(text(renderToStaticMarkup(<AnalysisPanel analysis={PCR} />))).not.toMatch(VERDICT_WORDS);
  });
});
