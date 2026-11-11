import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { AnalysesResponse } from "./analyses";
import { ChainAnalyticsView } from "./ChainAnalyticsV2";
import viewSource from "./ChainAnalyticsV2.tsx?raw";
import slotSource from "./AnalysisSlotV2.tsx?raw";
import {
  type AnalyticsKey, type ChainAnalytics, SECTIONS, analyticsOf, atmIndex, buildUpTone, lakhs, lakhsChange, signTone, signed,
  strikeBars, thresholdOf, windowRows,
} from "./chainAnalytics";

/**
 * M96 N1 (T1): the chain analytics view shows the server's numbers only - per section, OI in lakhs, tags coloured by
 * the option's price move, the user's own threshold highlight only when he set one, "not enough history" for the IV
 * rank, the no-broker state with no numbers - and its words hold no advice.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const ADVICE = /\b(recommend\w*|advise|should|must|buy|buying|sell|selling|guarantee\w*|best|safe bet|target\w*|top pick)\b/i;
const noop = () => undefined;

const ANALYTICS: ChainAnalytics = {
  note: "Information only, not advice.", snapshots: 1, since: "2026-10-21T03:50:00+00:00",
  buildup: {
    state: "ok", method: "Build-up method.", message: null, reference_time: "2026-10-21T03:50:00+00:00",
    counts: { call: { LONG_BUILDUP: 1 }, put: { SHORT_BUILDUP: 1 } },
    rows: [
      { strike: 100, call_price_change: 2, call_oi_change: 500000, call_class: "LONG_BUILDUP", call_label: "Long build-up",
        put_price_change: -1, put_oi_change: 3000000, put_class: "SHORT_BUILDUP", put_label: "Short build-up" },
      { strike: 110, call_price_change: 1, call_oi_change: -50000, call_class: "SHORT_COVERING", call_label: "Short covering",
        put_price_change: null, put_oi_change: null, put_class: "NOT_AVAILABLE", put_label: "Not available" },
    ],
    futures: { state: "ok", method: "m", message: null, since: "2026-10-21T03:50:00+00:00",
      points: [
        { time: "2026-10-21T03:50:00+00:00", price: 100, oi: 1000000, price_change: 0, oi_change: 0, class: "NO_CLEAR_CHANGE", label: "No clear change" },
        { time: "2026-10-21T04:20:00+00:00", price: 102, oi: 1100000, price_change: 2, oi_change: 100000, class: "LONG_BUILDUP", label: "Long build-up" },
      ],
      latest: { time: "2026-10-21T04:20:00+00:00", price: 102, oi: 1100000, price_change: 2, oi_change: 100000, class: "LONG_BUILDUP", label: "Long build-up" } },
  },
  walls: { state: "ok", method: "Walls method.", message: null, strikes_in_every_snapshot: 2,
    now: { call_oi_strike: 110, call_oi: 6000000, put_oi_strike: 100, put_oi: 4500000, call_add_strike: 110, call_add: 1000000, put_add_strike: 100, put_add: 3000000 },
    series: [
      { time: "2026-10-21T03:50:00+00:00", spot: 104, call_oi_strike: 110, put_oi_strike: 100, call_add_strike: null, put_add_strike: null },
      { time: "2026-10-21T04:30:00+00:00", spot: 105, call_oi_strike: 110, put_oi_strike: 100, call_add_strike: 110, put_add_strike: 100 },
    ] },
  pcr: { state: "ok", method: "PCR method.", message: null, strikes: 2,
    oi: { put: 3500000, call: 5000000, pcr: 0.7 }, change_oi: { put: 50, call: 25, pcr: 2, basis: "broker" }, volume: { put: 525, call: 1280, pcr: 0.41 },
    per_strike: [{ strike: 100, call_oi: 1000000, put_oi: 2000000, pcr: 2 }, { strike: 110, call_oi: 0, put_oi: 100000, pcr: null }] },
  iv: { state: "ok", method: "IV method.", message: null, reference_time: "2026-10-21T03:50:00+00:00",
    rank: { atm_iv: 25, sessions: 20, min_sessions: 20, percentile: 75, rank: 78.9, low: 10, high: 29, first_day: "2026-09-01", last_day: "2026-09-20", enough: true },
    per_strike: [{ strike: 100, call_iv: 20, call_iv_change: 2, put_iv: 21, put_iv_change: -1 }] },
  exposure: { state: "ok", method: "Exposure method.", message: null, units: { dex: "rupees crore", gex: "rupees crore per 1% move of the spot" },
    lot_size: 50, spot: 105, net_dex: 12.5, net_gex: -3.25, flip: 110, flips: [110],
    rows: [
      { strike: 100, call_delta: 0.9, put_delta: -0.1, call_gamma: 0.01, put_gamma: 0.01, call_dex: 10, put_dex: -2, call_gex: 1, put_gex: -6, net_dex: 8, net_gex: -5, cumulative_gex: -5 },
      { strike: 110, call_delta: 0.3, put_delta: -0.7, call_gamma: 0.02, put_gamma: 0.02, call_dex: 6, put_dex: -1.5, call_gex: 2, put_gex: -0.25, net_dex: 4.5, net_gex: 1.75, cumulative_gex: -3.25 },
    ] },
  volume_oi: { state: "ok", method: "Volume method.", message: null, threshold: 2, flagged: 1,
    rows: [{ strike: 100, call_volume: 50, call_oi: 100, call_ratio: 0.5, call_unusual: false, put_volume: 500, put_oi: 200, put_ratio: 2.5, put_unusual: true }] },
};

const OK = { symbol: "DEMO", expiry: "2026-10-27", generated_at: "2026-10-21T04:30:00Z", state: "ok", message: null, analyses: [], spot: 105,
  analytics: ANALYTICS } as unknown as AnalysesResponse;

const view = (section: AnalyticsKey, data: AnalysesResponse | null = OK, each: number | null = null) =>
  renderToStaticMarkup(<ChainAnalyticsView data={data} error={null} loading={false} section={section} onSection={noop} each={each} onEach={noop} />);

describe("M96 N1 chain analytics: pure helpers", () => {
  it("OI is shown in lakhs, changes signed", () => {
    expect(lakhs(1250000)).toBe("12.50 L");
    expect(lakhs(50000)).toBe("0.50 L");
    expect(lakhs(null)).toBe("–");
    expect(lakhsChange(30000)).toBe("+0.30 L");
    expect(lakhsChange(-5000)).toBe("−0.05 L");
    expect(lakhsChange(0)).toBe("0.00 L");
    expect(signed(2)).toBe("+2.00");
    expect(signed(-1.5)).toBe("−1.50");
  });

  it("windows the rows around ATM by listed position and keeps every row for All", () => {
    const rows = [100, 110, 120, 130, 140, 150].map((strike) => ({ strike }));
    expect(atmIndex(rows.map((r) => r.strike), 125)).toBe(2);           // a tie goes to the lower strike
    expect(windowRows(rows, 125, 1).map((r) => r.strike)).toEqual([110, 120, 130]);
    expect(windowRows(rows, 101, 2).map((r) => r.strike)).toEqual([100, 110, 120]);
    expect(windowRows(rows, null, 1)).toHaveLength(6);
    expect(windowRows(rows, 125, null)).toHaveLength(6);
  });

  it("colours a tag by the option's own price move and a number by its sign", () => {
    expect([buildUpTone("LONG_BUILDUP"), buildUpTone("SHORT_COVERING"), buildUpTone("SHORT_BUILDUP"), buildUpTone("LONG_UNWINDING"),
      buildUpTone("NO_CLEAR_CHANGE"), buildUpTone("NOT_AVAILABLE")]).toEqual(["up", "up", "down", "down", "muted", "muted"]);
    expect([signTone(1), signTone(-1), signTone(0), signTone(null)]).toEqual(["up", "down", "muted", "muted"]);
  });

  it("reads his threshold: empty clears it, junk is refused, nothing is pre-set", () => {
    expect(thresholdOf("")).toEqual({ value: null, problem: null });
    expect(thresholdOf(" 2.5 ")).toEqual({ value: 2.5, problem: null });
    expect(thresholdOf("0").problem).toMatch(/above 0/);
    expect(thresholdOf("abc").problem).toMatch(/number/);
    expect(viewSource).toMatch(/useState\(""\)/);                       // the field starts empty
  });

  it("builds charts from the server's numbers only", () => {
    const c = strikeBars([{ strike: 100, v: 3 }], [{ key: "v", label: "V", tone: "up", value: (r) => r.v }], 101, "V");
    expect(c.series[0].points).toEqual([{ x: 100, y: 3 }]);
    expect(c.marker).toEqual({ x: 101, label: "Spot" });
  });

  it("finds the analytics only in an ok answer", () => {
    expect(analyticsOf(OK)).toBe(ANALYTICS);
    expect(analyticsOf({ ...OK, state: "no_broker" } as AnalysesResponse)).toBeNull();
    expect(analyticsOf(null)).toBeNull();
  });
});

describe("M96 N1 chain analytics: the view", () => {
  it("is mounted once in the analysis area", () => {
    expect(slotSource.match(/<ChainAnalyticsV2 /g)).toHaveLength(1);
  });

  it("shows six sections, one selected, and the not-advice line", () => {
    const html = view("buildup");
    expect(SECTIONS.map((s) => s.key)).toEqual(["buildup", "walls", "pcr", "iv", "exposure", "volume_oi"]);
    expect(html.match(/role="tab"/g)).toHaveLength(6);
    expect(html.match(/aria-selected="true"/g)).toHaveLength(1);
    expect(text(html)).toContain("Information only, not advice.");
  });

  it("build-up: tags per strike coloured by price move, OI change in lakhs, futures tag", () => {
    const html = view("buildup");
    const words = text(html);
    expect(words).toContain("Long build-up");
    expect(words).toContain("+5.00 L");
    expect(words).toContain("+30.00 L");
    expect(words).toContain("−0.50 L");
    expect(html).toMatch(/text-up[^>]*>Long build-up/);
    expect(html).toMatch(/text-down[^>]*>Short build-up/);
    expect(html).toContain('data-tile="futures-tag"');
    expect(html).toContain("How it is worked out");
  });

  it("phone 390: a mirrored table shows the strike first and one side at a time; plain tables pin their first column", () => {
    for (const key of ["buildup", "iv", "volume_oi"] as const) {
      const html = view(key);
      expect(html, key).toContain("data-phone-sides");
      const head = html.match(/<thead>[\s\S]*?<\/thead>/)![0];
      expect(head.match(/data-col="([^"]+)"/)![1], key).toBe("k-phone");                 // the strike is the first column
      expect(head, key).toMatch(/class="[^"]*sticky left-0[^"]*md:hidden[^"]*"[^>]*data-col="k-phone"|data-col="k-phone"/);
      expect(head, key).toMatch(/class="[^"]*hidden md:table-cell[^"]*"[^>]*>Strike</);   // the middle strike: from md only
      expect(head, key).toMatch(/hidden md:table-cell[^"]*"[^>]*>Put/);                   // puts wait for the toggle on a phone
    }
    for (const key of ["walls", "pcr", "exposure"] as const) {
      const html = view(key);
      expect(html, key).not.toContain("data-phone-sides");
      expect(html.match(/<thead>[\s\S]*?<\/thead>/)![0], key).toMatch(/<th[^>]*sticky left-0/);
    }
  });

  it("walls: highest OI and OI addition per side and the shift table", () => {
    const words = text(view("walls"));
    expect(words).toMatch(/Highest call OI 110 60\.00 L/);
    expect(words).toMatch(/Most put OI added 100 \+30\.00 L/);
    expect(words).toContain("Shift through the session (2 strikes in every snapshot)");
  });

  it("PCR: OI, change-in-OI and volume PCR and per strike (no ratio when call OI is 0)", () => {
    const words = text(view("pcr"));
    expect(words).toMatch(/OI PCR 0\.70/);
    expect(words).toMatch(/Change-in-OI PCR 2\.00/);
    expect(words).toMatch(/Volume PCR 0\.41/);
    expect(words).toMatch(/110 0\.00 L 1\.00 L –/);
  });

  it("IV: percentile and rank with the session count, and not enough history below the minimum", () => {
    expect(text(view("iv"))).toMatch(/IV percentile 75\.0 20 sessions IV rank 78\.9/);
    const short = { ...OK, analytics: { ...ANALYTICS, iv: { ...ANALYTICS.iv, rank: { ...ANALYTICS.iv.rank!, sessions: 4, enough: false, percentile: null, rank: null } } } } as unknown as AnalysesResponse;
    const words = text(view("iv", short));
    expect(words).toContain("Not enough history: 4 of 20 sessions stored.");
    expect(words).not.toContain("78.9");
  });

  it("DEX / GEX: net values in rupees crore, the flip level and the running total", () => {
    const html = view("exposure");
    const words = text(html);
    expect(words).toMatch(/Net DEX \(₹ Cr\) \+12\.50/);
    expect(words).toMatch(/Net GEX \(₹ Cr \/ 1%\) −3\.25/);
    expect(words).toMatch(/GEX flip 110/);
    expect(html).toMatch(/data-tile="net-gex"[\s\S]*?text-down/);
  });

  it("volume / OI: highlights only above his own threshold; none set = nothing highlighted", () => {
    const html = view("volume_oi");
    expect(html.match(/data-unusual="true"/g)).toHaveLength(1);
    const unset = { ...OK, analytics: { ...ANALYTICS, volume_oi: { ...ANALYTICS.volume_oi, threshold: null, flagged: 0,
      rows: ANALYTICS.volume_oi.rows!.map((r) => ({ ...r, put_unusual: false })) } } } as unknown as AnalysesResponse;
    const plain = view("volume_oi", unset);
    expect(plain).not.toContain("data-unusual");
    expect(text(plain)).toContain("Your threshold Off");
  });

  it("a section without data shows the server's sentence, never numbers", () => {
    const none = { ...OK, analytics: { ...ANALYTICS, exposure: { state: "no_data", method: "m", message: "The lot size is not known.", units: {}, rows: [] } } } as unknown as AnalysesResponse;
    const words = text(view("exposure", none));
    expect(words).toContain("The lot size is not known.");
    expect(words).not.toContain("Net DEX");
  });

  it("the no-broker state shows the plain sentence and no numbers", () => {
    const nb = { symbol: "DEMO", expiry: null, generated_at: "x", state: "no_broker", message: "Connect your broker and log in to it today.", analyses: [] } as unknown as AnalysesResponse;
    const html = view("buildup", nb);
    expect(html).toContain('data-chain-analytics="no_broker"');
    expect(text(html)).toContain("Connect your broker");
    expect(html).not.toMatch(/\d+\.\d+ L/);
  });

  it("holds no advice words in any section", () => {
    for (const s of SECTIONS) {
      const words = text(view(s.key)).replace("not advice", "");
      expect(words.match(ADVICE), s.key).toBeNull();
    }
  });
});
