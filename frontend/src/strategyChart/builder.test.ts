import { describe, expect, it } from "vitest";
import { blankSettings, conditionIds, emptyDraft, exportFile, newCondition, nextId, numberOrNull, parseImport, serialize, toDraft, updateGroup, type Draft } from "./builder";

/** SFC P5: the builder's model (DEF-0, the tree edits, what the server receives, export / import). */
function filled(): Draft {
  const d = emptyDraft();
  d.name = " EMA cross ";
  d.timeframe = "5m";
  d.entry.long = { op: "AND", items: [
    { id: "c1", left: { kind: "indicator", block: "EMA", params: { x: "close", n: 9 } }, cmp: "crosses_above",
      right: { kind: "indicator", block: "EMA", params: { x: "close", n: 21, extra: "" } } },
    { op: "OR", items: [{ id: "c2", left: { kind: "pa", block: "RangeBreakout", params: { side: "up" } }, cmp: "is_true" }] },
    { op: "AND", items: [] },
  ] };
  return d;
}

describe("SFC P5: the builder model", () => {
  it("DEF-0: a new strategy has no name, timeframe, condition or exit chosen for him", () => {
    const d = emptyDraft();
    expect([d.name, d.timeframe, d.entry.long?.items, d.exit.stop, d.exit.target, d.entry.window, d.entry.max_entries_per_day, d.options])
      .toEqual(["", null, [], null, null, null, null, null]);
    expect(newCondition("c1")).toEqual({ id: "c1", left: null, cmp: null });
    expect(numberOrNull("")).toBeNull();
    expect(numberOrNull("0")).toBe(0);
  });

  it("ids are unique across the trees; a nested group is edited in place", () => {
    const d = filled();
    expect(conditionIds(d)).toEqual(["c1", "c2"]);
    expect(nextId(d)).toBe("c3");
    const g = updateGroup(d.entry.long!, [1], (inner) => ({ ...inner, op: "AND" }));
    expect((g.items[1] as { op: string }).op).toBe("AND");
    expect((d.entry.long!.items[1] as { op: string }).op).toBe("OR");                 // the original is not changed
  });

  it("the server receives exactly his choices: blanks and empty groups left out, nothing filled in", () => {
    const out = serialize(filled());
    expect(out).toEqual({
      schema: "atp.strategy/2", version: 1, name: "EMA cross", timeframe: "5m",
      entry: { sides: "long", long: { op: "AND", items: [
        { id: "c1", left: { kind: "indicator", block: "EMA", params: { x: "close", n: 9 } }, cmp: "crosses_above",
          right: { kind: "indicator", block: "EMA", params: { x: "close", n: 21 } } },
        { op: "OR", items: [{ id: "c2", left: { kind: "pa", block: "RangeBreakout", params: { side: "up" } }, cmp: "is_true" }] },
      ] } },
    });
    expect(out).not.toHaveProperty("exit");                                              // no exit chosen: none sent
    const d = filled();
    d.exit.stop = { kind: "atr", value: 1.5, atr_period: null };
    expect((serialize(d).exit as Record<string, unknown>).stop).toEqual({ kind: "atr", value: 1.5 });   // incomplete: the server refuses
  });

  it("a short-only strategy sends no long tree; export and import give the same strategy back", () => {
    const d = filled();
    d.entry.sides = "short";
    d.entry.short = { op: "AND", items: [{ id: "s1", left: { kind: "price", field: "close" }, cmp: "lt", right: { kind: "value", value: 100 } }] };
    const out = serialize(d);
    expect(out.entry).toEqual({ sides: "short", short: { op: "AND", items: [
      { id: "s1", left: { kind: "price", field: "close" }, cmp: "lt", right: { kind: "value", value: 100 } }] } });
    const file = exportFile(d);
    expect(file.name).toBe("ema-cross.atp-strategy.json");
    const back = parseImport(file.text);
    expect("draft" in back && serialize(back.draft)).toEqual(out);
    expect(parseImport("not json")).toEqual({ error: "This is not a strategy file (it is not JSON)." });
    expect(parseImport('{"name": "x"}')).toEqual({ error: "This is not an ATP strategy file (atp.strategy/2)." });
    expect(toDraft({ schema: "atp.strategy/2", name: "x" }).exit.stop).toBeNull();
  });

  it("DEF-0: a blank setting is listed, never left to the engine's own value (RSI with no length)", () => {
    const blocks = [{ block: "RSI", kind: "factor", returns: "num", unit: "index", doc: "", timeframed: true, values: [], kwargs: [],
      args: [{ name: "n", type: "window", required: false, choices: [], bounds: null }] }];
    const d = emptyDraft();
    d.entry.long = { op: "AND", items: [{ id: "c1", left: { kind: "indicator", block: "RSI", params: {} }, cmp: "gt", right: { kind: "value", value: null } },
      { id: "c2", left: null, cmp: null }] };
    expect(blankSettings(d, blocks)).toEqual(["c1: RSI needs its 'n' setting", "c1: enter the number", "c2: choose what it reads", "c2: choose the comparison"]);
    (d.entry.long.items[0] as { left: { params: Record<string, number> } }).left.params.n = 14;
    (d.entry.long.items[0] as { right: { value: number } }).right.value = 50;
    d.entry.long.items.pop();
    expect(blankSettings(d, blocks)).toEqual([]);
  });
});
