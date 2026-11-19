import { describe, expect, it } from "vitest";
import { exitWord, strategyMarkers, strategyStudies, type PlotReply } from "./model";

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
    const got = strategyStudies(REPLY, ["#1", "#2"], "custom_7");
    expect(got.map((s) => [s.id, s.line.pane, s.line.group])).toEqual([
      ["strategy:custom_7:s1", "price", undefined], ["strategy:custom_7:s2", "own", "strategy:own:RSI"], ["strategy:custom_7:s3", "own", "strategy:own:MACD"],
    ]);
    expect(got[0].line.data).toEqual([{ time: Date.parse("2026-11-18T04:00:00Z") / 1000, value: 100 }]);   // a missing value is not a point
  });

  it("long = arrow up below the bar, short skipped = a muted square, an exit = a circle by its result with its rule", () => {
    const [entry, skipped, exit] = strategyMarkers(REPLY);
    expect(entry).toMatchObject({ shape: "arrowUp", position: "belowBar", color: "up", text: "Long" });
    expect(skipped).toMatchObject({ shape: "square", position: "aboveBar", color: "fg", text: "" });
    expect(exit).toMatchObject({ shape: "circle", position: "aboveBar", color: "down", text: "Stop" });
    expect([exitWord("Target 1"), exitWord("Stop Loss"), exitWord("EXIT_SIGNAL"), exitWord("TIME_EXIT"), exitWord("Trailing stop"), exitWord(undefined)])
      .toEqual(["Target", "Stop", "Exit rule", "Time", "Trailing", "Exit"]);
  });
});
