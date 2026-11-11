/**
 * M99 P1: the Supply & Demand zones' new kinds and odds score on the chart - the tap card's score row with every part,
 * the higher-timeframe tag, the candle-group names per zone kind, the overlay's zone-kind and swap-level options (saved
 * with his chart settings) and the layer request that carries them.
 */
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import * as client from "../api/client";
import { DEFAULT_SD_OVERLAY, cleanSdOverlay } from "./sdStyles";
import { SdZonesBar } from "./SdZonesBar";
import { sdFocusLayer, sdPartNames, sdScoreRows, sdTipRows, sdZonesApi, type SdOdds, type SdZone } from "./sdZones";

const candle = (time: number, low: number, high: number) => ({ time, end: time + 10, low, high, range: high - low, body_pct: 80, mult: 1.8 });
const zone = (over: Partial<SdZone> = {}): SdZone => ({
  id: "sd-15m-abc", timeframe: "15m", side: "supply", formation: "SWING", state: "fresh", tests: 0, proximal: 102.8, distal: 105,
  base_start: 1100, base_end: 1100, formed: 1120, violated_at: null, leg_in: candle(1090, 101.5, 103),
  base: { candles: 1, times: [1100], high: 105, low: 102.6, max_body_pct: 64, max_mult: 1.2, bars: [candle(1100, 102.6, 105)] },
  leg_out: { ...candle(1110, 102, 104.6), of: "average range" }, label: "Supply · Swing · Fresh · 15m", kind: "swing", ...over,
});
const odds: SdOdds = {
  score: 71.7, leg_out_mult: 1.3, htf: { timeframe: "1d", relation: "with" },
  parts: [{ name: "fresh", value: 1, weight: 1 }, { name: "leg_out", value: 0.433, weight: 2 }, { name: "base_time", value: 1, weight: 1 },
          { name: "htf", value: 1, weight: 1 }, { name: "compression", value: 0, weight: 0 }],
};

describe("the odds score in the tap card", () => {
  it("shows the score, each counted part with its weight, and the higher-timeframe tag", () => {
    expect(sdScoreRows(odds)).toEqual([
      ["Score", "72 / 100"],
      ["Parts", "Fresh 100% · Move out 43% ×2.0 · Short base 100% · Higher timeframe 100%"],
      ["Higher timeframe", "With · D"],
    ]);
    const rows = sdTipRows(zone({ odds }));
    expect(rows.slice(0, 5).map((r) => r[0])).toEqual(["Formation", "State", "Score", "Parts", "Higher timeframe"]);
    expect(rows[0][1]).toBe("SWING (the swing's extreme candle)");
  });

  it("a part not counted shows a dash, no score shows a dash, an older server sends none", () => {
    const none: SdOdds = { ...odds, score: null, htf: null, parts: odds.parts.map((p) => ({ ...p, value: p.name === "htf" ? null : p.value })) };
    expect(sdScoreRows(none)[0]).toEqual(["Score", "–"]);
    expect(sdScoreRows(none)[1][1]).toContain("Higher timeframe –");
    expect(sdScoreRows(undefined)).toEqual([]);
    expect(sdTipRows(zone({ kind: undefined, formation: "DBR" })).map((r) => r[0])).toContain("Leg-out");
  });
});

describe("zone kinds", () => {
  it("names the candle groups by kind and highlights them under those names", () => {
    expect(sdPartNames({ kind: "swing" })).toEqual(["Before", "Zone candle", "Move out"]);
    expect(sdPartNames({ kind: "engulf" })).toEqual(["Before", "Zone candle", "Move out"]);
    expect(sdPartNames({ kind: "swap" })).toEqual(["Leg-in", "Base", "Break"]);
    expect(sdPartNames({ kind: "base" })).toEqual(["Leg-in", "Base", "Leg-out"]);
    expect(sdFocusLayer(zone()).marks?.map((m) => m.text)).toEqual(["Before", "Zone candle", "Move out"]);
  });

  it("the overlay options keep his zone kind and swap levels; a wrong value takes the reference", () => {
    expect(DEFAULT_SD_OVERLAY).toMatchObject({ kind: "base", swap: false });
    expect(cleanSdOverlay({ kind: "both", swap: true })).toMatchObject({ kind: "both", swap: true });
    expect(cleanSdOverlay({ kind: "all", swap: "yes" })).toMatchObject({ kind: "base", swap: false });
    const html = renderToStaticMarkup(<SdZonesBar chartTf="15min" shown={["15m"]} options={{ ...DEFAULT_SD_OVERLAY, kind: "both", swap: true }}
      onChange={() => undefined} note={null} />);
    expect(html).toContain('data-sd-kind="both"');
    expect(html).toContain("Swap levels");
    expect(html).toMatch(/data-sd-swap="[^"]*"[^>]*checked|checked=""[^>]*data-sd-swap/);
  });

  it("the layer request carries the zone kind and swap levels only when he changed them", async () => {
    const spy = vi.spyOn(client, "request").mockResolvedValue({} as never);
    await sdZonesApi.layer("NSE:NIFTY 50", ["15m"], false);
    await sdZonesApi.layer("NSE:NIFTY 50", ["15m", "1d"], true, null, "swing", true);
    expect(spy.mock.calls[0][0]).toBe("/charts/layers/sd-zones?symbol=NSE%3ANIFTY+50&timeframes=15m");
    expect(spy.mock.calls[1][0]).toBe("/charts/layers/sd-zones?symbol=NSE%3ANIFTY+50&timeframes=15m%2C1d&include_violated=1&kind=swing&swap=1");
    spy.mockRestore();
  });
});
