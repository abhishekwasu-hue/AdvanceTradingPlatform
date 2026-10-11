import { describe, expect, it } from "vitest";
import {
  blockProblems, defaultParams, duplicateStage, isCategory, isLive, isFilter, LEVEL_FIELDS, namesWhere, newBlock, whyChips, indicatorNames, isIndicator, moveStage, newStage, paramKinds, parseSymbols, screenText,
  runStamp, sameRun, stageAt, stageSpans, stageSummary, stageText, trail, type IndicatorStage, type Registry, type Stage,
} from "./model";
import contract from "./builderTexts.json";

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

function stage(over: Partial<IndicatorStage> = {}): IndicatorStage {
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

  it("shows the count after each stage, what it removed, and marks the stage that removed the last symbols", () => {
    expect(trail([a, off, b], funnel, true)).toEqual([
      { id: "a", survivors: 4, removed: 6, kills: false },
      { id: "off", survivors: null, removed: null, kills: false },
      { id: "b", survivors: 0, removed: 4, kills: true },
    ]);
  });

  it("shows no count when the run is stale, missing or of another shape", () => {
    expect(trail([a, b], funnel, false).every((r) => r.survivors === null && !r.kills)).toBe(true);
    expect(trail([a, b], null, true).every((r) => r.survivors === null && !r.kills)).toBe(true);
    expect(trail([a], funnel, true).every((r) => r.survivors === null)).toBe(true);
  });

  it("marks only the first stage that empties the funnel", () => {
    const zero = { universe: 3, with_data: 3, stages: [{ text: "RSI(14) > 60", survivors: 0 }, { text: "close > 100", survivors: 0 }] };
    expect(trail([a, b], zero, true).map((r) => r.kills)).toEqual([true, false]);
  });
});

describe("is this still the scan that ran", () => {
  const a = stage({ id: "a" });
  const b = stage({ id: "b", left: { fn: "close", params: {}, tf: null }, right: { kind: "value", value: 100 } });
  const ran = runStamp(REG, [a, b], "5m", ["TCS", "INFY"]);

  it("is, while the stages, the timeframe and the symbols are unchanged", () => {
    expect(ran.texts).toEqual(["RSI(14) > 60", "close > 100"]);
    expect(sameRun(ran, runStamp(REG, [a, b], "5m", ["TCS", "INFY"]))).toBe(true);
    expect(sameRun(ran, runStamp(REG, [a, { ...b, enabled: false }, b], "5m", ["TCS", "INFY"]))).toBe(true);  // a disabled stage is not in the scan
  });

  it("is not after any one of them changes (U5 review: counts of another universe or timeframe would mislead)", () => {
    expect(sameRun(ran, runStamp(REG, [a, { ...b, right: { kind: "value", value: 90 } }], "5m", ["TCS", "INFY"]))).toBe(false);
    expect(sameRun(ran, runStamp(REG, [b, a], "5m", ["TCS", "INFY"]))).toBe(false);
    expect(sameRun(ran, runStamp(REG, [a, b], "15m", ["TCS", "INFY"]))).toBe(false);
    expect(sameRun(ran, runStamp(REG, [a, b], "5m", ["TCS"]))).toBe(false);
    expect(sameRun(ran, runStamp(REG, [a, b], "5m", ["TCS", "INFY", "ABB"]))).toBe(false);
    expect(sameRun(ran, runStamp(REG, [a, b], "5m", ["TCS", "ABB"]))).toBe(false);
    expect(sameRun(null, ran)).toBe(false);
  });
});

describe("the builder-text contract with the server (builderTexts.json; tests/test_u5_funnel.py validates every text)", () => {
  it("writes exactly the text the server was shown, for every block kind, from the registry it describes", () => {
    const reg = contract.registry as unknown as Registry;
    expect(contract.cases.length).toBeGreaterThanOrEqual(20);
    for (const c of contract.cases) {
      expect(stageText(reg, { id: "x", enabled: true, ...c.stage } as unknown as Stage)).toBe(c.text);
    }
    const kinds = new Set(contract.cases.map((c) => (c.stage as { kind: string }).kind));
    expect([...kinds].sort()).toEqual(["category", "filter", "group", "indicator", "rank"]);
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
    (out[1] as IndicatorStage).left.params.n = 99;
    expect(list[0].left.params.n).toBe(14);
  });
  it("reads typed symbols: upper case, no repeats, at most the run limit", () => {
    expect(parseSymbols(" reliance, TCS;infy\nreliance  ")).toEqual(["RELIANCE", "TCS", "INFY"]);
    expect(parseSymbols(Array.from({ length: 60 }, (_, i) => `S${i}`).join(" ")).length).toBe(50);
  });
});

describe("D2 block forms", () => {
  const FULL = contract.registry as unknown as Registry;
  it("offers yes / no filters and classifiers as their own blocks (the crosses stay in the indicator block)", () => {
    // U5 D2 review: functions needing data the run path does not load (sector / index / F&O lists, the chain) are not offered
    expect(namesWhere(FULL, isFilter)).toEqual(["NearSupport", "Pattern", "ReversalAt"]);
    expect(namesWhere(FULL, isCategory)).toEqual(["SwingDirection", "Trend"]);
    expect(FULL.IsFnO.needs).toEqual(["reference"]);
    expect(paramKinds(FULL.NearSupport).map((p) => p.kind)).toEqual(["number", "window"]);
    expect(paramKinds(FULL.ReversalAt)[0]).toEqual({ kind: "series", name: "level", options: LEVEL_FIELDS });
    expect(paramKinds(FULL.IndexMember)[0].kind).toBe("text");
    expect(defaultParams(FULL.NearSupport)).toEqual({ tolerance_pct: 0.5, swing: 3 });
  });

  it("starts every kind from a usable block", () => {
    for (const kind of ["indicator", "filter", "category", "rank", "group"] as const) {
      const b = newBlock(FULL, kind);
      expect(b.kind).toBe(kind);
      expect(stageText(FULL, b).length).toBeGreaterThan(0);
    }
    const filter = newBlock(FULL, "filter");
    expect(filter.kind === "filter" && filter.call.fn).toBe("NearSupport");          // runs on bars, nothing to type first
    const cat = newBlock(FULL, "category");
    expect(cat.kind === "category" && cat.values.length).toBe(1);
  });

  const a = stage({ id: "a" });
  const inner1 = stage({ id: "i1", right: { kind: "value", value: 70 } });
  const inner2: Stage = { id: "i2", kind: "filter", call: { fn: "IsFnO", params: {}, tf: null }, enabled: true };
  const off: Stage = { id: "i3", kind: "filter", call: { fn: "Pattern", params: { name: "doji" }, tf: null }, enabled: false };
  const group: Stage = { id: "g", kind: "group", op: "ANY", children: [inner1, off, inner2], enabled: true };

  it("joins a group's enabled blocks inside one stage, and leaves out an empty group", () => {
    expect(screenText(FULL, [a, group])).toBe("RSI(14) > 60 AND (RSI(14) > 70 OR IsFnO())");
    const empty: Stage = { ...group, id: "e", children: [off] };
    expect(screenText(FULL, [a, empty])).toBe("RSI(14) > 60");
    expect(runStamp(FULL, [a, empty], "5m", []).texts).toEqual(["RSI(14) > 60"]);
    expect([a, empty, group].map(isLive)).toEqual([true, false, true]);
    expect(trail([a, empty], { universe: 3, with_data: 3, stages: [{ text: "RSI(14) > 60", survivors: 2 }] }, true)[1].survivors).toBeNull();
    expect(stageSummary(FULL, group)).toBe("any of: RSI(14) above 70; IsFnO()");
    expect(stageSummary(FULL, { ...group, op: "NOT" } as Stage)).toBe("none of: RSI(14) above 70; IsFnO()");
  });

  it("lands a validation problem on the innermost block it points at", () => {
    const text = screenText(FULL, [a, group]);
    expect(stageAt(FULL, [a, group], text.indexOf("70"))).toBe("i1");
    expect(stageAt(FULL, [a, group], text.indexOf("IsFnO"))).toBe("i2");
    expect(stageAt(FULL, [a, group], text.indexOf("OR"))).toBe("g");
    for (const sp of stageSpans(FULL, [a, group])) {
      const s = sp.id === "a" ? a : sp.id === "g" ? group : sp.id === "i1" ? inner1 : inner2;
      expect(text.slice(sp.start, sp.end)).toBe(stageText(FULL, s));
    }
  });

  it("duplicates a group with new ids all the way down", () => {
    const out = duplicateStage([group], "g");
    const copy = out[1];
    expect(copy.kind).toBe("group");
    if (copy.kind === "group") {
      expect(copy.children.map((c) => c.id)).not.toEqual(group.kind === "group" ? group.children.map((c) => c.id) : []);
      expect(new Set([...copy.children.map((c) => c.id), copy.id, "g", "i1", "i2", "i3"]).size).toBe(8);
    }
  });

  it("shows why a symbol matched - each stage it passed on its own - only for the stages that ran", () => {
    const funnel = { universe: 2, with_data: 2, stages: [], passes: { TCS: [true, false], INFY: [true] } };
    expect(whyChips(FULL, [a, group], funnel, "TCS")).toEqual([
      { id: "a", summary: "RSI(14) above 60", passed: true },
      { id: "g", summary: "any of: RSI(14) above 70; IsFnO()", passed: false },
    ]);
    expect(whyChips(FULL, [a, group], funnel, "INFY")).toEqual([]);                // another shape: show nothing
    expect(whyChips(FULL, [a, group], null, "TCS")).toEqual([]);
  });
});

describe("D2 review: blocks that cannot run yet, NOT spans", () => {
  const FULL = contract.registry as unknown as Registry;
  it("asks for a value or a name before a block can run (never `== \"\"`)", () => {
    const cat: Stage = { id: "c", kind: "category", call: { fn: "Trend", params: { swing: 3 }, tf: null }, values: [], negate: false, enabled: true };
    const idx: Stage = { id: "f", kind: "filter", call: { fn: "IndexMember", params: { index: "  " }, tf: null }, enabled: true };
    const ok: Stage = { id: "o", kind: "filter", call: { fn: "Pattern", params: { name: "doji" }, tf: null }, enabled: true };
    const group: Stage = { id: "g", kind: "group", op: "ANY", children: [cat, ok], enabled: true };
    expect(blockProblems(FULL, [group, idx, ok])).toEqual({ c: "Pick at least one value for Trend.", f: "Type the index for IndexMember." });
    expect(blockProblems(FULL, [{ ...idx, enabled: false }])).toEqual({});            // a switched-off block is not in the scan
  });

  it("maps every block of a NOT group to its own text", () => {
    const a = stage({ id: "a" });
    const b: Stage = { id: "b", kind: "filter", call: { fn: "Pattern", params: { name: "doji" }, tf: null }, enabled: true };
    const c = stage({ id: "c", right: { kind: "value", value: 30 }, op: "<" });
    const not: Stage = { id: "n", kind: "group", op: "NOT", children: [b, c], enabled: true };
    const text = screenText(FULL, [a, not]);
    expect(text).toBe('RSI(14) > 60 AND NOT (Pattern("doji") AND RSI(14) < 30)');
    const byId = Object.fromEntries(stageSpans(FULL, [a, not]).map((sp) => [sp.id, text.slice(sp.start, sp.end)]));
    expect(byId).toEqual({ a: "RSI(14) > 60", n: 'NOT (Pattern("doji") AND RSI(14) < 30)', b: 'Pattern("doji")', c: "RSI(14) < 30" });
    expect(stageAt(FULL, [a, not], text.indexOf("doji"))).toBe("b");
  });
});
