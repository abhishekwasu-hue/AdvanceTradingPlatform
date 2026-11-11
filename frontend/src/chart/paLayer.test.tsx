import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import chartsSource from "./ChartsPageV2.tsx?raw";
import { MENU_ITEMS } from "./indicatorsModel";
import { PACKS } from "../screener/taxonomy";
import { DEFAULT_PA_OPTIONS, EVIDENCE_LAYERS, PA_LAYER_ID, PaBar, isPaReply, paMarkTime, paMarker, paMarkers, paTimeframe, type PaReply,
         type PaState } from "./paLayer";

/** M99 P6: the price patterns layer - marks from the server's readings, the Why? list, the evidence rows. Made-up bars only. */
const T = (d: number) => new Date(Date.UTC(2026, 1, 2 + d, 3, 45)).toISOString();
const CANDLES = Array.from({ length: 6 }, (_, i) => ({ timestamp: T(i) }));
const REPLY: PaReply = {
  available: true, reason: null, base_tf: "1d", note: "", settings_note: "reference - edit before use",
  marks: [
    { time: T(1), origin_time: T(1), name: "inside_bar", label: "Inside bar", short: "IB", side: "", variant: "",
      text: "Inside bar: its high and low stayed within the bar before - a pause; context only.", evidence: [] },
    { time: T(2), origin_time: T(1), name: "trap", label: "Trap beyond a swing extreme", short: "TR", side: "down", variant: "trap",
      text: "Trap at a swing high: the swing went above the swing high before it, then a close came back under it.", evidence: [] },
    { time: T(4), origin_time: T(3), name: "head_shoulders", label: "Inverse head and shoulders", short: "HS", side: "up", variant: "",
      text: "Inverse head and shoulders: after a fall, three swings with the middle one beyond both.",
      evidence: [{ layer: "adx", title: "ADX / DMI", text: "ADX / DMI (14): DI+ above DI-; ADX 27 and rising.", agrees: true,
        agree_word: "agrees with the reading's side" }] },
  ],
  levels: [{ kind: "support", low: 99.5, high: 100.5, touches: 4, bars_since: 2, role_reversal: false, score: 55, three_touches: true }],
};
const STATE = (r: PaReply | null, extra: Partial<PaState> = {}): PaState =>
  ({ reply: r, loading: false, failed: false, markers: [], unsupported: false, ...extra });

describe("price patterns layer", () => {
  it("maps the chart timeframes the layer reads", () => {
    expect(paTimeframe("day")).toBe("1d");
    expect(paTimeframe("60min")).toBe("1h");
    expect(paTimeframe("month")).toBeNull();
  });

  it("puts up under the bar, down over it and a context reading over it in a neutral tone", () => {
    expect(paMarker({ side: "up", short: "HS" }, T(1))).toMatchObject({ position: "belowBar", color: "up", text: "HS" });
    expect(paMarker({ side: "down", short: "TR" }, T(1))).toMatchObject({ position: "aboveBar", color: "down", text: "TR" });
    expect(paMarker({ side: "", short: "IB" }, T(1))).toMatchObject({ position: "aboveBar", color: "fg" });
    expect(paMarkers(REPLY, CANDLES).map((x) => x.text)).toEqual(["IB", "TR", "HS"]);
    expect(paMarkers({ ...REPLY, available: false, marks: [] }, CANDLES)).toEqual([]);
    expect(isPaReply(REPLY)).toBe(true);
    expect(isPaReply({})).toBe(false);
  });

  it("lists the newest readings first, each with its Why? and its evidence rows, and the swing areas", () => {
    const html = renderToStaticMarkup(<PaBar state={STATE(REPLY)} options={DEFAULT_PA_OPTIONS} onChange={() => undefined} />);
    expect(html.indexOf("Inverse head and shoulders")).toBeLessThan(html.indexOf("Inside bar"));
    expect(html).toContain("Why?");
    expect(html).toContain("Agrees");
    expect(html).toContain("Nearest swing areas");
    expect(html).toContain("Evidence");
    expect(html).not.toMatch(/\b(buy|sell|target|stop)\b/i);
  });

  it("offers the evidence layers off by default", () => {
    expect(DEFAULT_PA_OPTIONS.evidence).toEqual([]);
    const html = renderToStaticMarkup(<PaBar state={STATE(REPLY)} options={DEFAULT_PA_OPTIONS} onChange={() => undefined} />);
    for (const layer of EVIDENCE_LAYERS) expect(html).toContain(`data-pa-evidence="${layer}"`);
    expect(html).not.toContain("checked");
  });

  it("says why nothing is drawn", () => {
    const empty = renderToStaticMarkup(<PaBar state={STATE({ ...REPLY, marks: [], levels: [] })} options={DEFAULT_PA_OPTIONS} onChange={() => undefined} />);
    expect(empty).toContain("No pattern in the bars shown.");
    const tf = renderToStaticMarkup(<PaBar state={STATE(null, { unsupported: true })} options={DEFAULT_PA_OPTIONS} onChange={() => undefined} />);
    expect(tf).toContain("1m to Weekly");
  });

  it("is a layer behind its flag, off until turned on, and the screen gallery has its pack chip", () => {
    const item = MENU_ITEMS.find((i) => i.id === PA_LAYER_ID);
    expect(item).toMatchObject({ group: "layers", flag: "pa_pack", label: "Price patterns" });
    expect(PACKS.pa).toBe("Price patterns");
    expect(chartsSource).toContain("features.on(\"pa_pack\")");
    expect(paMarkTime(T(1), "1d")).toMatch(/2026/);
  });
});
