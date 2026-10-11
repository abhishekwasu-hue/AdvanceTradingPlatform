import { describe, expect, it } from "vitest";
import {
  defaultParams, duplicateStage, indicatorNames, isIndicator, moveStage, newStage, paramKinds, parseSymbols, screenText,
  stageAt, stageSpans, stageSummary, stageText, trail, type Registry, type Stage,
} from "./model";

// A slice of the real registry (GET /api/screener/registry) - shapes as the server sends them.
const REG: Registry = {
  close: { kind: "factor", returns: "num", unit: "price", args: [], types: {}, defaults: {}, choices: {}, values: [], varargs: false, timeframed: true, field: true, doc: "" },
  volume: { kind: "factor", returns: "num", unit: "volume", args: [], types: {}, defaults: {}, choices: {}, values: [], varargs: false, timeframed: true, field: true, doc: "" },
  RSI: { kind: "factor", returns: "num", unit: "index", args: ["n"], types: { n: "window" }, defaults: { n: 14 }, choices: {}, values: [], varargs: false, timeframed: true, field: false, doc: "" },
  EMA: { kind: "factor", returns: "num", unit: "same", args: ["x", "n"], types: { x: "num", n: "window" }, defaults: {}, choices: {}, values: [], varargs: false, timeframed: true, field: false, doc: "" },
  Supertrend: { kind: "factor", returns: "num", unit: "price", args: ["n", "k"], types: { n: "window", k: "num" }, defaults: { n: 10, k: 3 }, choices: {}, values: [], varargs: false, timeframed: true, field: false, doc: "" },
  SwingLow: { kind: "factor", returns: "num", unit: "price", args: ["degree"], types: { degree: "num" }, defaults: { degree: 0 }, choices: { degree: [0, 1, 2] }, values: [], varargs: false, timeframed: true, field: false, doc: "" },
  ORHigh: { kind: "factor", returns: "num", unit: "price", args: ["minutes"], types: { minutes: "window" }, defaults: {}, choices: {}, values: [], varargs: false, timeframed: true, field: false, doc: "" },
  Rank: { kind: "factor", returns: "num", unit: "count", args: ["x"], types: { x: "num" }, defaults: {}, choices: {}, values: [], varargs: false, timeframed: false, cross_sectional: true, field: false, doc: "" },
  Count: { kind: "factor", returns: "num", unit: "count", args: ["n", "cond"], types: { n: "window", cond: "bool" }, defaults: {}, choices: {}, values: [], varargs: false, timeframed: true, field: false, doc: "" },
  Greatest: { kind: "factor", returns: "num", unit: "same", args: ["x"], types: { x: "num" }, defaults: {}, choices: {}, values: [], varargs: true, timeframed: false, field: false, doc: "" },
  Pattern: { kind: "filter", returns: "bool", unit: null, args: ["name"], types: { name: "str" }, defaults: {}, choices: { name: ["doji"] }, values: [], varargs: false, timeframed: true, field: false, doc: "" },
};

function stage(over: Partial<Stage> = {}): Stage {
  return { ...newStage(REG), ...over };
}

describe("which entries an indicator block can hold", () => {
  it("takes per-bar numbers with editable arguments, not filters, cross-sectional ranks, vararg or condition functions", () => {
    expect(indicatorNames(REG)).toEqual(["close", "volume", "EMA", "ORHigh", "RSI", "Supertrend", "SwingLow"]);
    for (const n of ["Rank", "Count", "Greatest", "Pattern"]) expect(isIndicator(REG[n])).toBe(false);
  });

  it("edits each argument the way it is typed", () => {
    expect(paramKinds(REG.EMA).map((p) => p.kind)).toEqual(["series", "window"]);
    expect(paramKinds(REG.Supertrend).map((p) => p.kind)).toEqual(["window", "number"]);
    expect(paramKinds(REG.SwingLow)[0]).toEqual({ kind: "select", name: "degree", options: [0, 1, 2] });
    expect(defaultParams(REG.EMA)).toEqual({ x: "close", n: 20 });
    expect(defaultParams(REG.ORHigh)).toEqual({ minutes: 15 });
    expect(defaultParams(REG.Supertrend)).toEqual({ n: 10, k: 3 });
  });
});

describe("ScreenQL from stages", () => {
  it("writes each comparison, timeframe and cross the way the parser reads it", () => {
    const rsi = stage({ left: { fn: "RSI", params: { n: 14 }, tf: "15m" } });
    expect(stageText(REG, rsi)).toBe("RSI(14)@15m > 60");
    const cross = stage({ left: { fn: "close", params: {}, tf: null }, op: "crosses above", right: { kind: "series", ref: { fn: "EMA", params: { x: "close", n: 20 }, tf: null } } });
    expect(stageText(REG, cross)).toBe("CrossAbove(close, EMA(close, 20))");
    const st = stage({ left: { fn: "Supertrend", params: { n: 10, k: 2.5 }, tf: "1h" }, op: "<=", right: { kind: "series", ref: { fn: "close", params: {}, tf: null } } });
    expect(stageText(REG, st)).toBe("Supertrend(10, 2.5)@1h <= close");
    expect(stageText(REG, stage({ right: { kind: "value", value: 0.1 + 0.2 } }))).toBe("RSI(14) > 0.3");
  });

  it("joins only the enabled stages with AND, and maps a problem position back to its stage", () => {
    const a = stage({ id: "a" });
    const b = stage({ id: "b", enabled: false });
    const c = stage({ id: "c", left: { fn: "close", params: {}, tf: "1d" }, op: ">", right: { kind: "value", value: 100 } });
    const text = screenText(REG, [a, b, c]);
    expect(text).toBe("RSI(14) > 60 AND close@1d > 100");
    expect(stageSpans(REG, [a, b, c])).toEqual([{ id: "a", start: 0, end: 12 }, { id: "c", start: 17, end: 31 }]);
    expect(stageAt(REG, [a, b, c], text.indexOf("100"))).toBe("c");
    expect(stageAt(REG, [a, b, c], 0)).toBe("a");
    expect(stageAt(REG, [a, b, c], null)).toBeNull();
    expect(screenText(REG, [b])).toBe("");
  });

  it("says each stage in plain words", () => {
    expect(stageSummary(REG, stage({ left: { fn: "RSI", params: { n: 14 }, tf: "15m" } }))).toBe("RSI(14) on 15m above 60");
    expect(stageSummary(REG, stage({ left: { fn: "close", params: {}, tf: null }, op: "crosses below", right: { kind: "series", ref: { fn: "EMA", params: { x: "close", n: 50 }, tf: "1d" } } })))
      .toBe("close crosses below EMA(close, 50) on 1d");
    expect(stageSummary(REG, stage({ op: "<=", right: { kind: "value", value: 30 } }))).toBe("RSI(14) at or below 30");
  });
});

describe("the survivor trail", () => {
  const a = stage({ id: "a" });
  const b = stage({ id: "b", left: { fn: "close", params: {}, tf: null }, right: { kind: "value", value: 100 } });
  const off = stage({ id: "off", enabled: false });
  const funnel = { universe: 12, with_data: 10, stages: [{ text: "RSI(14) > 60", survivors: 4 }, { text: "close > 100", survivors: 0 }] };
  const ran = ["RSI(14) > 60", "close > 100"];

  it("shows the count after each stage, what it removed, and marks the stage that removed the last symbols", () => {
    expect(trail([a, off, b], funnel, ran, REG)).toEqual([
      { id: "a", survivors: 4, removed: 6, kills: false },
      { id: "off", survivors: null, removed: null, kills: false },
      { id: "b", survivors: 0, removed: 4, kills: true },
    ]);
  });

  it("drops every count once a stage changed since the run (an old number would mislead)", () => {
    const edited = { ...b, right: { kind: "value" as const, value: 90 } };
    expect(trail([a, edited], funnel, ran, REG).every((r) => r.survivors === null)).toBe(true);
    expect(trail([a, b], null, null, REG).every((r) => r.survivors === null && !r.kills)).toBe(true);
  });

  it("marks only the first stage that empties the funnel", () => {
    const zero = { universe: 3, with_data: 3, stages: [{ text: "RSI(14) > 60", survivors: 0 }, { text: "close > 100", survivors: 0 }] };
    expect(trail([a, b], zero, ran, REG).map((r) => r.kills)).toEqual([true, false]);
  });
});

describe("editing the list", () => {
  const list = [stage({ id: "a" }), stage({ id: "b" }), stage({ id: "c" })];
  it("moves a stage by one and stays put at the ends", () => {
    expect(moveStage(list, "b", -1).map((s) => s.id)).toEqual(["b", "a", "c"]);
    expect(moveStage(list, "a", -1)).toBe(list);
    expect(moveStage(list, "c", 1)).toBe(list);
  });
  it("duplicates a stage right after itself with a new id and its own copy of the parameters", () => {
    const out = duplicateStage(list, "a");
    expect(out.map((s) => s.id.startsWith("a"))).toEqual([true, true, false, false]);
    expect(out[1].id).not.toBe("a");
    out[1].left.params.n = 99;
    expect(list[0].left.params.n).toBe(14);
  });
  it("reads typed symbols: upper case, no repeats, at most the run limit", () => {
    expect(parseSymbols(" reliance, TCS;infy\nreliance  ")).toEqual(["RELIANCE", "TCS", "INFY"]);
    expect(parseSymbols(Array.from({ length: 60 }, (_, i) => `S${i}`).join(" ")).length).toBe(50);
  });
});
