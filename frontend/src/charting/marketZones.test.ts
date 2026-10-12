import { afterEach, describe, expect, it, vi } from "vitest";
import cases from "./__fixtures__/chart_bands_cases.json";
import { bandShapes, type Converters } from "./geometry";
import {
  bandsAt, bandsLayer, distancePct, marketZonesApi, roleAt, selectBands, touchText, zoneLabel,
  type MarketZonesLayerResponse, type ZoneCandidate,
} from "./marketZones";

interface Case { name: string; price: number; max_bands: number; max_distance_pct: number | null; candidates: ZoneCandidate[];
                 expected: { zone_key: string; role: string; label: string; touch_text: string }[] }

const cand = (key: string, low: number, high: number, extra: Partial<ZoneCandidate> = {}): ZoneCandidate => ({
  zone_key: key, zone_type: low === high ? "SUPPORT" : "DEMAND_ZONE", family: "DEMAND_ZONE", timeframe: "1h", kind: low === high ? "line" : "band",
  low, high, low_text: low.toFixed(2), high_text: high.toFixed(2), state: "fresh", touches: 0, strength: 50, ...extra,
});

describe("selectBands (Z7a-3) - the same rule as the backend", () => {
  // __fixtures__/chart_bands_cases.json is a byte-identical copy of backend/tests/fixtures/zones/chart_bands_cases.json
  // (checked by backend/tests/test_zones_chart_screenql.py; rebuild both with `python -m tests.zones_chart_cases --write`)
  it("matches every shared case from the backend rule", () => {
    const all = (cases as unknown as { per_role: number; cases: Case[] }).cases;
    expect(all.length).toBeGreaterThanOrEqual(20);
    for (const c of all) {
      const got = selectBands(c.candidates, c.price, c.max_bands, (cases as { per_role: number }).per_role, c.max_distance_pct);
      expect(got.map((b) => [b.zone_key, b.role, b.label, b.touch_text]), c.name)
        .toEqual(c.expected.map((e) => [e.zone_key, e.role, e.label, e.touch_text]));
      expect(got.length).toBeLessThanOrEqual(6);
    }
  });

  it("keeps three per role by proximity, top to bottom, and gives a short side's slots away", () => {
    const sup = [0, 1, 2, 3, 4].map((i) => cand(`s${i}`, 1001 + 5 * i, 1003 + 5 * i));
    const got = selectBands([...sup, cand("d", 980, 985)], 1000);
    expect(got.map((b) => b.zone_key)).toEqual(["s4", "s3", "s2", "s1", "s0", "d"]);
    expect(got[4].label).toBe("supply 1001.00 (fresh)");
    expect(got[5].label).toBe("demand 985.00 (fresh)");
    expect(selectBands(sup, 1000, 2).map((b) => b.zone_key)).toEqual(["s1", "s0"]);
    expect(selectBands(sup, 0)).toEqual([]);
  });

  it("shows one band per overlapping cluster of a role (the strongest)", () => {
    const got = selectBands([cand("weak", 992, 997, { strength: 40 }), cand("strong", 990, 995, { strength: 80 })], 1000);
    expect(got.map((b) => b.zone_key)).toEqual(["strong"]);
  });

  it("roles, distances, labels and touch counts", () => {
    expect(roleAt(cand("x", 1050, 1060), 1000)).toBe("supply");
    expect(roleAt(cand("x", 1050, 1060), 1100)).toBe("demand");
    expect(distancePct(cand("x", 990, 1010), 1000)).toBe(0);
    expect(distancePct(cand("x", 1010, 1020), 1000)).toBeCloseTo(1, 10);
    expect(zoneLabel(cand("x", 512.3, 512.3, { state: "tested" }), "supply")).toBe("supply 512.30 (tested)");
    expect([0, 1, 4].map(touchText)).toEqual(["no touches", "1 touch", "4 touches"]);
  });
});

describe("the layer and its shapes", () => {
  const conv: Converters = { x: (t) => t, y: (p) => 1000 - p * 5, width: 400, height: 300 };

  it("draws a band as a tinted rectangle, a line as a segment, labels at the right edge", () => {
    const bands = selectBands([cand("b", 100, 102, { touches: 2, state: "tested" }), cand("l", 98, 98)], 101);
    const layer = bandsLayer(bands, "2026-10-09T10:00:00Z");
    expect(layer.asOf).toBe("2026-10-09T10:00:00Z");
    expect(layer.bands?.map((b) => b.label)).toEqual(["demand 102.00 (tested) · 2 touches", "demand 98.00 (fresh) · no touches"]);
    const shapes = bandShapes(layer.bands ?? [], conv);
    expect(shapes.map((s) => s.type)).toEqual(["rect", "text", "segment", "text"]);
    const rect = shapes[0] as Extract<(typeof shapes)[number], { type: "rect" }>;
    expect(rect).toMatchObject({ x: 0, y: 490, w: 400, h: 10 });
    expect(rect.fill).toMatch(/^#16a34a[0-9a-f]{2}$/);
    expect(shapes[1]).toMatchObject({ type: "text", x: 396, align: "right", text: "demand 102.00 (tested) · 2 touches" });
    expect(shapes[2]).toMatchObject({ type: "segment", y1: 510, y2: 510, x2: 400, dash: "solid" });
    expect(bandShapes([{ low: 1, high: 2 }], { ...conv, y: () => null })).toEqual([]);
    // the platform's default-settings zones served as a fallback say so on every band
    expect(bandsLayer(bands, null, true).bands?.map((b) => b.label)).toEqual([
      "demand 102.00 (tested) · 2 touches · default settings", "demand 98.00 (fresh) · no touches · default settings"]);
  });

  it("re-picks from the response only when the price on screen moved", () => {
    const zones = [cand("a", 99, 99.5), cand("b", 100.5, 101)];
    const layer: MarketZonesLayerResponse = { instrument: "NSE:X", at: "", as_of: null, scope: "platform", price: 100, price_source: "snapshot",
      bands: selectBands(zones, 100), zones, live_count: 2, max_bands: 6, note: "" };
    expect(bandsAt(layer, 100)).toBe(layer.bands);
    expect(bandsAt(layer, null)).toBe(layer.bands);
    expect(bandsAt(layer, 101.5).map((b) => b.role)).toEqual(["demand", "demand"]);
  });
});

describe("marketZonesApi", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("asks the chart layer endpoint with the instrument and optional price", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ bands: [], zones: [] }), { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    await marketZonesApi.layer("NSE:NIFTY 50", { price: 25000.5, maxBands: 4 });
    const url = String((fetchMock.mock.calls[0] as unknown as [string])[0]);
    expect(url).toContain("/charts/layers/market-zones?");
    expect(url).toContain("instrument=NSE%3ANIFTY+50");
    expect(url).toContain("price=25000.5");
    expect(url).toContain("max_bands=4");
  });
});
