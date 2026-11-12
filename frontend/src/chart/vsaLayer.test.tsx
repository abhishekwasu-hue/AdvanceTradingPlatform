import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import chartsSource from "./ChartsPageV2.tsx?raw";
import { MENU_ITEMS } from "./indicatorsModel";
import { PACKS } from "../screener/taxonomy";
import { DEFAULT_VSA_OPTIONS, VSA_LAYER_ID, VsaBar, isVsaReply, markTime, vsaMarker, vsaMarkers, vsaTimeframe, type VsaReply,
         type VsaState } from "./vsaLayer";

/** M99 P3: the volume spread layer - marks from the server's readings, the Why? list and the switches. Made-up bars only. */
const T = (d: number) => new Date(Date.UTC(2026, 1, 2 + d, 3, 45)).toISOString();
const CANDLES = Array.from({ length: 6 }, (_, i) => ({ timestamp: T(i) }));
const REPLY: VsaReply = {
  available: true, reason: null, base_tf: "1d", note: "", settings_note: "reference - edit before use",
  marks: [
    { time: T(2), origin_time: T(2), name: "no_demand", label: "No demand", short: "ND", side: "weakness", variant: "",
      text: "No demand: narrow spread (0.52 x the average range of 20 bars), low volume. It suggests weakness." },
    { time: T(4), origin_time: T(4), name: "test", label: "Test", short: "T", side: "strength", variant: "",
      text: "Test: average spread, low volume, close in the high third. It suggests strength." },
  ],
};
const STATE = (r: VsaReply | null, extra: Partial<VsaState> = {}): VsaState =>
  ({ reply: r, loading: false, failed: false, markers: [], unsupported: false, ...extra });

describe("volume spread layer", () => {
  it("maps the chart timeframes the layer reads", () => {
    expect(vsaTimeframe("day")).toBe("1d");
    expect(vsaTimeframe("15min")).toBe("15m");
    expect(vsaTimeframe("month")).toBeNull();
  });

  it("puts strength under the bar and weakness over it, with the short label", () => {
    expect(vsaMarker({ side: "strength", short: "SC" }, T(1))).toMatchObject({ position: "belowBar", color: "up", text: "SC" });
    expect(vsaMarker({ side: "weakness", short: "UT" }, T(1))).toMatchObject({ position: "aboveBar", color: "down", text: "UT" });
    const m = vsaMarkers(REPLY, CANDLES);
    expect(m.map((x) => x.text)).toEqual(["ND", "T"]);
    expect(vsaMarkers({ ...REPLY, available: false, marks: [] }, CANDLES)).toEqual([]);
    expect(isVsaReply(REPLY)).toBe(true);
    expect(isVsaReply({})).toBe(false);
  });

  it("lists the newest readings first, each with its Why?", () => {
    const html = renderToStaticMarkup(<VsaBar state={STATE(REPLY)} options={DEFAULT_VSA_OPTIONS} onChange={() => undefined} />);
    expect(html.indexOf("Test")).toBeLessThan(html.indexOf("No demand"));
    expect(html).toContain("Why?");
    expect(html).toContain("It suggests weakness");
    expect(html).toContain("Background");
    expect(html).toContain("Next-bar confirmation");
    expect(html).not.toMatch(/\b(buy|sell|target|stop)\b/i);
  });

  it("says no volume data and draws nothing when the bars have none", () => {
    const html = renderToStaticMarkup(<VsaBar state={STATE({ ...REPLY, available: false, reason: "no volume data", marks: [] })}
      options={DEFAULT_VSA_OPTIONS} onChange={() => undefined} />);
    expect(html).toContain("No volume data");
    expect(html).not.toContain("data-testid=\"vsa-marks\"");
  });

  it("is a layer behind its flag, off until turned on, and the screen gallery has its pack chip", () => {
    const item = MENU_ITEMS.find((i) => i.id === VSA_LAYER_ID);
    expect(item).toMatchObject({ group: "layers", flag: "vsa_pack", label: "Volume spread" });
    expect(PACKS.vsa).toBe("Volume spread");
    expect(chartsSource).toContain("features.on(\"vsa_pack\")");
    expect(markTime(T(1), "1d")).toMatch(/2026/);
  });
});
