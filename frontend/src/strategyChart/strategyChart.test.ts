import { describe, expect, it } from "vitest";
import { exitWord, strategyLayer, strategyMarkers, strategyStudies, whyAt, type PlotReply } from "./model";

/** SFC P1: the chart parts from the server's one evaluation (made-up reply). */
const REPLY: PlotReply = {
  strategy_id: "custom:7", name: "EMA cross", timeframe: "5m", available: true, reason: null, state: "waiting", last_signal: null,
  series: [
    { id: "s1", label: "EMA(close, 9)", pane: "price", points: [{ t: "2026-11-18T04:00:00+00:00", v: 100 }, { t: "2026-11-18T04:05:00+00:00", v: null }] },
    { id: "s2", label: "RSI(14)", pane: "own:RSI", points: [{ t: "2026-11-18T04:00:00+00:00", v: 55 }] },
    { id: "s3", label: "MACD(close)", pane: "own:MACD", points: [] },
  ],
  markers: [
    { id: "m1", t: "2026-11-18T04:00:00+00:00", kind: "entry", side: "long" },
    { id: "m2", t: "2026-11-18T04:10:00+00:00", kind: "skipped", side: "short" },
    { id: "m3", t: "2026-11-18T04:20:00+00:00", kind: "exit", side: "long", rule: "STOP_LOSS", pnl: -12 },
  ],
};

describe("SFC P1: the strategy on the chart", () => {
  it("draws only its own indicators: price lines on the price pane, each other family in its own pane", () => {
    const got = strategyStudies(REPLY, "#1", "custom_7");
    expect(got.map((s) => [s.id, s.line.pane, s.line.group])).toEqual([
      ["strategy:custom_7:s1", "price", undefined], ["strategy:custom_7:s2", "own", "strategy:own:RSI"], ["strategy:custom_7:s3", "own", "strategy:own:MACD"],
    ]);
    expect(got[0].line.data).toEqual([{ time: Date.parse("2026-11-18T04:00:00Z") / 1000, value: 100 }]);   // a missing value is not a point
    expect(new Set(got.map((s) => s.line.color))).toEqual(new Set(["#1"]));                                // P2: the strategy's own colour
  });

  it("P2: a fixed number is a flat line in its series' pane, across the closed bars", () => {
    const times = [100, 200, 300];
    const got = strategyStudies({ ...REPLY, levels: [{ id: "l1", value: 30, pane: "own:RSI", label: "RSI(14) 30" }] }, "#2", "k", times);
    const level = got.find((s) => s.id === "strategy:k:l1");
    expect(level?.line).toMatchObject({ pane: "own", group: "strategy:own:RSI", color: "#2", title: "RSI(14) 30" });
    expect(level?.line.data).toEqual(times.map((time) => ({ time, value: 30 })));
  });

  it("P2: the open trade - entry, his stop and target as lines, profit / loss sides from the entry on; no target: none drawn", () => {
    const tones = { up: "green", down: "red" };
    const pos = { side: "long" as const, entry_time: "2026-11-18T04:00:00+00:00", entry: 100, stop: 98, target: 106 };
    const layer = strategyLayer({ ...REPLY, position: pos }, "#3", tones);
    expect(layer.lines?.map((l) => [l.title, l.price, l.color])).toEqual([["Entry", 100, "#3"], ["SL", 98, "red"], ["Target", 106, "green"]]);
    expect(layer.bands?.map((b) => [b.id, b.low, b.high, b.role, b.start])).toEqual([
      ["position-loss", 98, 100, "supply", Date.parse(pos.entry_time) / 1000], ["position-profit", 100, 106, "demand", Date.parse(pos.entry_time) / 1000]]);
    const noTarget = strategyLayer({ ...REPLY, position: { ...pos, side: "short", stop: 103, target: null } }, "#3", tones);
    expect(noTarget.lines?.map((l) => l.title)).toEqual(["Entry", "SL"]);                                 // OQ-10: never a made-up target
    expect(noTarget.bands?.map((b) => b.role)).toEqual(["demand"]);
  });

  it("P2: a breakout box is its four edges and its level; a zone band starts at its own start", () => {
    const layer = strategyLayer({ ...REPLY, pa: [
      { id: "p1", kind: "box", low: 10, high: 12, label: "range high 12", from: "2026-11-18T04:00:00+00:00", to: "2026-11-18T05:00:00+00:00", level: 12 },
      { id: "p2", kind: "band", low: 8, high: 9, label: "Demand", from: "2026-11-18T03:00:00+00:00", to: "2026-11-18T05:00:00+00:00", role: "DEMAND" },
    ] }, "#4", { up: "g", down: "r" });
    expect(layer.segments).toHaveLength(5);
    expect(layer.bands).toEqual([expect.objectContaining({ low: 8, high: 9, role: "demand", start: Date.parse("2026-11-18T03:00:00Z") / 1000 })]);
  });

  it("P2: the pending hint is a dimmed circle on the forming candle only", () => {
    const pending = { t: "2026-11-18T04:25:00+00:00", side: "short" as const };
    expect(strategyMarkers({ ...REPLY, pending }, "2026-11-18T04:25:00+00:00").slice(-1)[0]).toMatchObject({ shape: "circle", color: "fg", text: "Forming", position: "aboveBar" });
    expect(strategyMarkers({ ...REPLY, pending }, "2026-11-18T04:30:00+00:00")).toHaveLength(3);          // another candle: nothing
    expect(strategyMarkers({ ...REPLY, pending }, null)).toHaveLength(3);
  });

  it("long = arrow up below the bar, short skipped = a muted square, an exit = a circle by its result with its rule", () => {
    const [entry, skipped, exit] = strategyMarkers(REPLY);
    expect(entry).toMatchObject({ shape: "arrowUp", position: "belowBar", color: "up", text: "Long" });
    expect(skipped).toMatchObject({ shape: "square", position: "aboveBar", color: "fg", text: "" });
    expect(exit).toMatchObject({ shape: "circle", position: "aboveBar", color: "down", text: "Stop" });
    expect([exitWord("Target 1"), exitWord("Stop Loss"), exitWord("EXIT_SIGNAL"), exitWord("TIME_EXIT"), exitWord("Trailing stop"), exitWord(undefined)])
      .toEqual(["Target", "Stop", "Exit rule", "Time", "Trailing", "Exit"]);
  });

  it("P3 (T7): a signal marker lists its rule's conditions; an exit names its rule and result; nothing elsewhere", () => {
    const reply: PlotReply = { ...REPLY, why: { m1: [
      { id: "c1", held: true, optional: false, text: "EMA(close, 9) crossed above EMA(close, 21)", values: { left: 101, right: 99 }, timeframe: "5m" },
      { id: "c2", held: false, optional: true, text: "RSI(14) > 45", values: { left: 40, right: 45 }, timeframe: "5m" }] } };
    const at = (iso: string) => Date.parse(iso) / 1000;
    const [entry] = whyAt(reply, at("2026-11-18T04:00:00Z"));
    expect(entry).toMatchObject({ title: "Long entry", tone: "up" });
    expect(entry.rows.map((r) => [r.held, r.optional])).toEqual([[true, false], [false, true]]);
    expect(whyAt(reply, at("2026-11-18T04:10:00Z"))[0]).toMatchObject({ title: "Short signal · not taken (already in a trade)", tone: "muted" });
    expect(whyAt(reply, at("2026-11-18T04:20:00Z"))[0]).toMatchObject({ title: "Long exit · Stop", tone: "down", rule: "STOP_LOSS", pnl: -12 });
    expect(whyAt(reply, at("2026-11-18T04:05:00Z"))).toEqual([]);
    expect(whyAt(reply, null)).toEqual([]);
  });
});
