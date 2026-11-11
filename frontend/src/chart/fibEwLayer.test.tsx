import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import chartsSource from "./ChartsPageV2.tsx?raw";
import { MENU_ITEMS } from "./indicatorsModel";
import { PACKS } from "../screener/taxonomy";
import { COUNT_NOTE, DEFAULT_FIB_EW_SHOW, FIB_EW_LAYER_ID, FibEwBar, fibEwLayerData, fibEwMarker, fibEwMarkers, fibEwTimeframe, isFibEwReply,
         type FibEwReply, type FibEwState } from "./fibEwLayer";

/** M99 P5: the Fibonacci + Elliott layer - the tool's levels, the clusters, the count with its lines, the marks and Why?. Made-up bars only. */
const T = (h: number) => new Date(Date.UTC(2026, 1, 2, 3 + h, 45)).toISOString();
const CANDLES = Array.from({ length: 12 }, (_, i) => ({ timestamp: T(i) }));
const REPLY: FibEwReply = {
  available: true, reason: null, base_tf: "1h", note: "", settings_note: "reference - edit before use",
  fib: [{ kind: "retrace", ratio: 0.618, price: 104, label: "retrace 0.618 of the last swing", from_time: T(2), anchor_time: T(6) },
        { kind: "golden_projection", ratio: 0.5, price: 96, label: "golden projection 0.5 of the swing before", from_time: T(2), anchor_time: T(6) }],
  clusters: [{ low: 101, high: 102, count: 3, labels: ["a", "b", "c"], sources: ["last swing", "swing before"], side: "up", from_time: T(6) }],
  count: {
    type: "ZIGZAG", type_words: "Zigzag", state: "COR_C", clarity: "CLEAR", direction: -1, labels: ["0", "A", "B", "C"],
    points: [{ time: T(1), price: 110 }, { time: T(4), price: 100 }, { time: T(6), price: 106 }, { time: T(10), price: 98 }],
    kind: "zigzag",
    checks: [{ rule: "b_within_a", words: "Wave B does not go beyond the start of wave A", status: "pass", kind: "hard" },
             { rule: "c_within_b", words: "Wave C does not go back beyond the end of wave B", status: "open", kind: "hard" }],
    lines: [{ price: 106, words: "Count void beyond this level (the count's own rule)", kind: "count" }],
    thrust: null, note: "Count is an interpretation",
  },
  marks: [
    { time: T(5), origin_time: T(1), name: "cluster_touch", side: "up", short: "FIB", label: "Price reached a Fibonacci cluster (3 levels)",
      text: "The falling swing reached a cluster of 3 Fibonacci levels from 2 different swings. A reaction area to watch, not a forecast." },
    { time: T(11), origin_time: T(1), name: "zigzag_c_entry", side: "up", short: "ZZ-C", label: "Zigzag wave C may be complete",
      text: "A zigzag correction down (waves A-B-C). Count is an interpretation; a description of past bars, not advice." },
  ],
};
const STATE = (r: FibEwReply | null, extra: Partial<FibEwState> = {}): FibEwState =>
  ({ reply: r, loading: false, failed: false, markers: [], unsupported: false, ...extra });

describe("Fibonacci + Elliott layer", () => {
  it("puts up readings under the bar and down readings over it, a void count as a circle", () => {
    expect(fibEwMarker({ side: "up", short: "W3", name: "wave3_entry" }, T(1))).toMatchObject({ position: "belowBar", color: "up", text: "W3", shape: "square" });
    expect(fibEwMarker({ side: "down", short: "VOID", name: "count_void" }, T(1))).toMatchObject({ position: "aboveBar", color: "down", shape: "circle" });
    expect(fibEwMarkers(REPLY, CANDLES).map((m) => m.text)).toEqual(["FIB", "ZZ-C"]);
    expect(fibEwMarkers({ ...REPLY, available: false, marks: [] }, CANDLES)).toEqual([]);
    expect(isFibEwReply(REPLY)).toBe(true);
    expect(isFibEwReply({ available: true, marks: [] })).toBe(false);
    expect(fibEwTimeframe("60min")).toBe("1h");
    expect(fibEwTimeframe("month")).toBeNull();
  });

  it("draws the levels, the cluster band, the count path with its labels and its void line - each part on its own toggle", () => {
    const d = fibEwLayerData(REPLY, CANDLES);
    expect(d?.bands).toHaveLength(1);
    expect(d?.bands?.[0]).toMatchObject({ low: 101, high: 102, role: "demand" });
    expect(d?.labels?.map((l) => l.text)).toEqual(["A", "B", "C"]);
    const segs = d?.segments ?? [];
    expect(segs.filter((s) => s.style === "dotted")).toHaveLength(2);                 // the two Fibonacci levels
    expect(segs.some((s) => s.label === "Count void" && s.p1 === 106)).toBe(true);
    expect(segs.filter((s) => s.width === 2).map((s) => s.style)).toEqual(["solid", "solid", "dashed"]);   // the running leg dashed
    const onlyFib = fibEwLayerData(REPLY, CANDLES, { ...DEFAULT_FIB_EW_SHOW, clusters: false, count: false });
    expect(onlyFib?.bands).toHaveLength(0);
    expect(onlyFib?.labels).toHaveLength(0);
    expect(fibEwLayerData({ ...REPLY, available: false }, CANDLES)).toBeNull();
  });

  it("labels a thrust as a projection, never a target", () => {
    const count = { ...REPLY.count!, thrust: { price: 120, from_time: T(10), words: "Thrust projection after the triangle - a minimum estimate, not a target" } };
    const d = fibEwLayerData({ ...REPLY, count }, CANDLES);
    expect(d?.segments?.find((s) => s.p1 === 120)?.label).toBe("Thrust projection (not a target)");
    const html = renderToStaticMarkup(<FibEwBar state={STATE({ ...REPLY, count })} show={DEFAULT_FIB_EW_SHOW} onShow={() => undefined} />);
    expect(html).toContain("a minimum estimate, not a target");
  });

  it("shows the count, its rule checklist, the newest marks first with their Why? and the interpretation note", () => {
    const html = renderToStaticMarkup(<FibEwBar state={STATE(REPLY)} show={DEFAULT_FIB_EW_SHOW} onShow={() => undefined} />);
    expect(html).toContain("Zigzag · wave C running");
    expect(html).toContain(COUNT_NOTE);
    expect(html).toContain("data-status=\"pass\"");
    expect(html).toContain("data-status=\"open\"");
    expect(html.indexOf("Zigzag wave C may be complete")).toBeLessThan(html.indexOf("Price reached a Fibonacci cluster"));
    expect(html).toContain("Why?");
    expect(html).not.toMatch(/\b(buy|sell|stop-loss|stop loss)\b/i);
  });

  it("is a layer behind its flag, and the screen gallery has its pack chip", () => {
    expect(MENU_ITEMS.find((i) => i.id === FIB_EW_LAYER_ID)).toMatchObject({ group: "layers", flag: "fib_ew_pack", label: "Fibonacci + Elliott" });
    expect(PACKS.fib_ew).toBe("Fibonacci + Elliott");
    expect(chartsSource).toContain("features.on(\"fib_ew_pack\")");
  });
});
