import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { OiBannerRow } from "../oi/types";
import { OiByStrike, OiHistory, OiTabView, type OiTabState } from "./OiTabV2";
import { OI_BANNER, OI_HISTORY, OI_STRIKES } from "./oiTab.fixture";
import {
  SIDES, VERDICT_WORDS, changeLine, dataAsOf, highestOi, historyRows, lastChangeAt, nextUpdateText, oiRead, readWords, shouldPoll,
  shownRows, sourceTimes,
} from "./oiTab";

const r = (slot: string, call: number, put: number, over: Partial<OiBannerRow> = {}): OiBannerRow => ({
  ...OI_HISTORY.rows[0], slot: `2026-10-30T${slot}:00+05:30`, data_as_of: `2026-10-30T${slot}:00+05:30`, total_call_oi: call, total_put_oi: put,
  phase: "session", partial: false, ...over,
});
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
const STATE: OiTabState = { banner: OI_BANNER, history: OI_HISTORY, strikes: OI_STRIKES, error: null, loading: false };
const NOW = Date.parse("2026-10-30T22:41:00+05:30");          // a Friday evening: the next open is Monday

describe("M61 OI-2: the OI history shows only the readings that changed", () => {
  // newest first: 15:35 after close (same), 15:30 (same as 15:25), 15:25 (changed), 15:20 (changed), 09:15 first
  const rows = [r("15:35", 300, 400, { phase: "after_close" }), r("15:30", 300, 400), r("15:25", 300, 400), r("15:20", 290, 400), r("09:15", 100, 100)];
  const views = historyRows(rows);
  it("computes the change against the reading before it", () => {
    expect(views.map((v) => [v.dCall, v.dPut])).toEqual([[0, 0], [0, 0], [10, 0], [190, 300], [null, null]]);
    expect(views.map((v) => v.changed)).toEqual([false, false, true, true, true]);
  });
  it("keeps only changed session rows; Show all keeps every row", () => {
    expect(shownRows(views, false).map((v) => v.row.slot.slice(11, 16))).toEqual(["15:25", "15:20", "09:15"]);
    expect(shownRows(views, true)).toHaveLength(5);
    expect(lastChangeAt(views)).toBe("15:25");
  });
  it("leaves pre-open, after-close and partial readings out of the default view, labelled", () => {
    const odd = historyRows([r("11:55", 120, 120), r("11:50", 80, 110, { partial: true, strikes: 24 }), r("11:45", 120, 110), r("06:45", 90, 90, { phase: "pre_open" })]);
    expect(shownRows(odd, false).map((v) => v.row.slot.slice(11, 16))).toEqual(["11:55", "11:45"]);
    expect(odd[1].read).toBe("Partial reading (24 strikes) · Call OI down 40, Put OI unchanged");
    expect(odd[3].read).toBe("Pre-open reading · First reading");
  });
  it("reads each row in full words", () => {
    expect(readWords(r("10:00", 1, 1), 21_60_470, -30_000, false)).toBe("Call OI up 21.6 L, Put OI down 30,000");
    expect(readWords(r("10:00", 1, 1), 0, 0, false)).toBe("Call OI unchanged, Put OI unchanged");
  });
  it("after the close: 'Last change at HH:MM' and the view chips frozen", () => {
    const html = renderToStaticMarkup(<OiHistory views={views} date="2026-10-30" interval={5} onInterval={() => {}} closed />);
    expect(text(html)).toContain("Last change at 15:25");
    expect(html.match(/<button[^>]*disabled=""[^>]*>\d+ min/g)).toHaveLength(3);
    expect(text(html)).toContain("3 of 5 readings: only those where call OI or put OI changed");
    expect(html).toContain("ΔCall");
    expect(html).not.toMatch(/truncate/);
    const open = renderToStaticMarkup(<OiHistory views={views} date="2026-10-30" interval={5} onInterval={() => {}} closed={false} />);
    expect(open).not.toContain("Last change at");
    expect(open).not.toMatch(/disabled=""/);
  });
  it("on a phone the extra columns are picked (only Time, Call OI, Put OI, PCR show by default)", () => {
    const html = renderToStaticMarkup(<OiHistory views={views} date={null} interval={5} onInterval={() => {}} closed={false} />);
    expect(html).toContain("data-oi-columns");
    expect(html).toMatch(/<th[^>]*hidden md:table-cell[^>]*>ΔCall/);
    expect(html).not.toMatch(/<th[^>]*hidden md:table-cell[^>]*>PCR/);
  });
});

describe("M61 OI-3: OI by strike", () => {
  const html = renderToStaticMarkup(<OiByStrike series={OI_STRIKES.strikes} atm={23_050} />);
  it("rotates every strike label (no overlap), marks ATM and gives the figures on hover", () => {
    expect(html.match(/rotate\(-45 /g)).toHaveLength(11);
    expect(html).toMatch(/data-atm="true"/);
    expect(text(html)).toContain("ATM");
    expect(html).toContain("<title>23,200: Call OI 27.49 L / Put OI 9.85 L</title>");
    expect(text(html)).toContain("23,050: Call OI 18.45 L / Put OI 35.2 L");       // the ATM strike until one is hovered
  });
  it("uses the page's one order and colour: calls first in red, puts second in green", () => {
    expect(SIDES.map((s) => s.label)).toEqual(["Call OI", "Put OI"]);
    expect(SIDES[0].fill).toBe("fill-oi-call");
    expect(SIDES[1].fill).toBe("fill-oi-put");
    expect(html.indexOf("Call OI</span>")).toBeLessThan(html.indexOf("Put OI</span>"));
  });
});

describe("M61 OI-4: one data time for the tab; a closed market is not polled", () => {
  it("is the oldest of the sources shown", () => {
    const sources = sourceTimes(OI_BANNER, OI_HISTORY, OI_STRIKES);
    expect(sources.map((s) => s.label)).toEqual(["Summary", "OI history", "OI by strike"]);
    expect(dataAsOf(sources)).toBe("Data as of 15:30");
    expect(dataAsOf([{ label: "x", at: null }])).toBeNull();
  });
  it("says when the next update comes, from the exchange calendar's next open", () => {
    expect(nextUpdateText("2026-11-02T09:15:00+05:30", NOW)).toBe("Next update on Mon 2 Nov at 09:15");
    expect(nextUpdateText("2026-10-31T09:15:00+05:30", NOW)).toBe("Next update tomorrow at 09:15");
    expect(nextUpdateText("2026-10-30T09:15:00+05:30", Date.parse("2026-10-30T07:00:00+05:30"))).toBe("Next update at 09:15");
    expect(nextUpdateText(null, NOW)).toBe("Next update when the market opens");
  });
  it("polls only while the market is open", () => {
    expect(shouldPoll(true, true)).toBe(true);
    expect(shouldPoll(true, null)).toBe(true);
    expect(shouldPoll(false, true)).toBe(false);
    expect(shouldPoll(true, false)).toBe(false);
  });
  it("shows the one time, its sources behind (?), Market closed and the next update", () => {
    const html = renderToStaticMarkup(<OiTabView underlying="NIFTY" state={STATE} interval={5} onInterval={() => {}} statusOpen={false}
      nextOpen="2026-11-02T09:15:00+05:30" now={NOW} />);
    const t = text(html);
    expect(t).toContain("Data as of 15:30");
    expect(html).toContain('aria-label="Help: Data times"');
    expect(t).toContain("Market closed");
    expect(t).toContain("Next update on Mon 2 Nov at 09:15");
    expect(t.match(/Data as of/g)).toHaveLength(1);
  });
});

describe("M61 OI-5: the summary card is numbers first, in neutral words", () => {
  it("reads put / call OI without a verdict", () => {
    expect(oiRead({ total_call_oi: 100, total_put_oi: 104, pcr: 1.04 })).toBe("Put OI above call OI (PCR 1.04)");
    expect(oiRead({ total_call_oi: 120, total_put_oi: 100, pcr: 0.83 })).toBe("Call OI above put OI (PCR 0.83)");
    expect(oiRead({ total_call_oi: 100, total_put_oi: 100, pcr: 1 })).toBe("Put OI equal to call OI (PCR 1.00)");
  });
  it("says 'No OI change since HH:MM' instead of 'Flat/unclear'", () => {
    const views = historyRows([r("15:35", 300, 400), r("15:30", 300, 400), r("15:25", 300, 400), r("15:20", 280, 390)]);
    expect(changeLine(views)).toBe("No OI change since 15:25");
    expect(changeLine(historyRows([r("15:25", 290, 400), r("15:20", 280, 390)]))).toBe("Last reading: Call OI up 10, Put OI up 10");
  });
  it("finds the highest call and put OI strikes", () => {
    expect(highestOi(OI_STRIKES.strikes, "call")).toEqual({ strike: 23_200, oi: 27_49_000 });
    expect(highestOi(OI_STRIKES.strikes, "put")).toEqual({ strike: 23_000, oi: 49_44_000 });
  });
  it("shows PCR, max pain, DTE and the highest call / put OI, calls before puts", () => {
    const html = renderToStaticMarkup(<OiTabView underlying="NIFTY" state={STATE} interval={5} onInterval={() => {}} statusOpen={false}
      nextOpen={null} now={NOW} />);
    const t = text(html);
    for (const k of ["pcr", "max_pain", "dte", "high_call", "high_put"]) expect(html).toContain(`data-oi-number="${k}"`);
    expect(html.indexOf('data-oi-number="high_call"')).toBeLessThan(html.indexOf('data-oi-number="high_put"'));
    expect(t).toContain("Max pain 23,000");
    expect(t).toContain("Highest call OI 23,200 27.49 L");
    expect(t).toContain("Highest put OI 23,000 49.44 L");
    expect(t).toMatch(/Put OI above call OI \(PCR \d\.\d\d\) · No OI change since 15:25/);
  });
});

describe("M61 OI-6: information only", () => {
  it("no buy / sell / bullish / bearish / overbought word anywhere on the tab", () => {
    const html = renderToStaticMarkup(<OiTabView underlying="NIFTY" state={STATE} interval={5} onInterval={() => {}} statusOpen={false}
      nextOpen={null} now={NOW} />);
    const shown = renderToStaticMarkup(<OiHistory views={historyRows(OI_HISTORY.rows)} date={null} interval={5} onInterval={() => {}} closed />);
    for (const h of [html, shown]) expect(text(h)).not.toMatch(VERDICT_WORDS);
    // the OI figures are in L / Cr, never full digits
    expect(text(html)).not.toMatch(/\d,\d\d,\d\d,\d{3}/);
  });
});
