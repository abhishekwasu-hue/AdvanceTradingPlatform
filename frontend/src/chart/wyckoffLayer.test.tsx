import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import chartsSource from "./ChartsPageV2.tsx?raw";
import { MENU_ITEMS } from "./indicatorsModel";
import { PACKS } from "../screener/taxonomy";
import { WYCKOFF_LAYER_ID, WyckoffBar, isWyckoffReply, wyckoffLayerData, wyckoffMarker, wyckoffMarkers, type WyckoffReply,
         type WyckoffState } from "./wyckoffLayer";

/** M99 P4: the Wyckoff layer - the range box, the phase letters, the event marks and the Why? list. Made-up bars only. */
const T = (h: number) => new Date(Date.UTC(2026, 1, 2, 3 + h, 45)).toISOString();
const CANDLES = Array.from({ length: 12 }, (_, i) => ({ timestamp: T(i) }));
const REPLY: WyckoffReply = {
  available: true, reason: null, base_tf: "1h", note: "", settings_note: "reference - edit before use", bias: "",
  ranges: [{ start_time: T(1), end_time: T(11), known_time: T(3), top: 110, bottom: 100, after: "fall", last_phase: "C", lean: "strength", open: true }],
  phases: [{ time: T(1), phase: "A", words: "Phase A - the old move stops", lean: "" },
           { time: T(5), phase: "B", words: "Phase B - the range is built", lean: "" },
           { time: T(9), phase: "C", words: "Phase C - the shake and its test", lean: "strength" }],
  marks: [
    { time: T(3), origin_time: T(2), name: "ar", label: "Automatic rally", short: "AR", side: "strength", variant: "", phase: "A",
      text: "Automatic rally: the first swing back after the climax. It suggests strength; it is a description of the bars, not advice." },
    { time: T(9), origin_time: T(9), name: "shake", label: "Spring", short: "SPR", side: "strength", variant: "class 3", phase: "C",
      text: "Spring: beyond the range edge and back inside within 1 bar(s). It suggests strength; it is a description of the bars, not advice." },
  ],
};
const STATE = (r: WyckoffReply | null, extra: Partial<WyckoffState> = {}): WyckoffState =>
  ({ reply: r, loading: false, failed: false, markers: [], unsupported: false, ...extra });

describe("Wyckoff layer", () => {
  it("puts strength under the bar and weakness over it, with the short label", () => {
    expect(wyckoffMarker({ side: "strength", short: "SPR" }, T(1))).toMatchObject({ position: "belowBar", color: "up", text: "SPR" });
    expect(wyckoffMarker({ side: "weakness", short: "UTAD" }, T(1))).toMatchObject({ position: "aboveBar", color: "down", text: "UTAD" });
    expect(wyckoffMarkers(REPLY, CANDLES).map((m) => m.text)).toEqual(["AR", "SPR"]);
    expect(wyckoffMarkers({ ...REPLY, available: false, marks: [] }, CANDLES)).toEqual([]);
    expect(isWyckoffReply(REPLY)).toBe(true);
    expect(isWyckoffReply({ available: true, marks: [] })).toBe(false);
  });

  it("draws each range as a box from its climax to its last bar, with the phase letters inside it", () => {
    const d = wyckoffLayerData(REPLY, CANDLES);
    expect(d?.segments).toHaveLength(4);
    const [top, bottom] = d?.segments ?? [];
    expect(top).toMatchObject({ p1: 110, p2: 110, style: "dashed", t1: Date.parse(T(1)) / 1000, t2: Date.parse(T(11)) / 1000 });
    expect(bottom).toMatchObject({ p1: 100, p2: 100 });
    expect(d?.labels?.map((l) => l.text)).toEqual(["A", "B", "C"]);
    expect(wyckoffLayerData({ ...REPLY, available: false }, CANDLES)).toBeNull();
  });

  it("lists the newest events first, each with its Why?, and the current phase", () => {
    const html = renderToStaticMarkup(<WyckoffBar state={STATE(REPLY)} />);
    expect(html.indexOf("Spring")).toBeLessThan(html.indexOf("Automatic rally"));
    expect(html).toContain("Why?");
    expect(html).toContain("Phase C - the shake and its test");
    expect(html).toContain("leans up");
    expect(html).not.toMatch(/\b(buy|sell|target|stop)\b/i);
  });

  it("says no volume data and draws nothing when the bars have none", () => {
    const html = renderToStaticMarkup(<WyckoffBar state={STATE({ ...REPLY, available: false, reason: "no volume data", marks: [], ranges: [], phases: [] })} />);
    expect(html).toContain("No volume data");
    expect(html).not.toContain("data-testid=\"wyckoff-marks\"");
  });

  it("is a layer behind its flag, and the screen gallery has its pack chip", () => {
    expect(MENU_ITEMS.find((i) => i.id === WYCKOFF_LAYER_ID)).toMatchObject({ group: "layers", flag: "wyckoff_pack", label: "Wyckoff" });
    expect(PACKS.wyckoff).toBe("Wyckoff");
    expect(chartsSource).toContain("features.on(\"wyckoff_pack\")");
  });
});
