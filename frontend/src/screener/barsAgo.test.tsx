import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import contract from "./builderTexts.json";
import { stagesFromServer, type AstNode, type ServerStage } from "./importStages";
import {
  barsAgo, barsAgoWords, MAX_BARS_AGO, newStage, parseBarsAgo, screenText, seriesRef, stageSummary, stageText, withBarsAgo,
  type IndicatorStage, type Registry, type Stage,
} from "./model";
import { BarsAgoInline } from "./ParamInline";
import { StageRow } from "./StageRow";

/** FN2-b (docs/design/FN2_GAP_AUDIT.md row 6): "n bars ago" on the indicator block - blank is the current bar, 1 to the
 * server's MAX_OFFSET a bar before it; it writes `x[n]` (offset before timeframe, as the server writes it) and a screen
 * the server splits opens back into the same block. */
const reg = contract.registry as unknown as Registry;
const num = (v: number): AstNode => ({ k: "Num", v });
const field = (name: string, offset = 0, tf: string | null = null): AstNode => ({ k: "Field", name, offset, tf });
const call = (name: string, args: AstNode[], offset = 0, tf: string | null = null): AstNode => ({ k: "Call", name, args, kwargs: {}, offset, tf });
const item = (text: string, ast: AstNode): ServerStage => ({ text, wrapped: text, ast });

describe("FN2-b: bars ago in the model", () => {
  it("keeps the most bars back equal to the server's MAX_OFFSET", () => {
    const src = readFileSync(new URL("../../../backend/app/screener/validator.py", import.meta.url), "utf8");
    expect(Number(/^MAX_OFFSET = (\d+)/m.exec(src)?.[1])).toBe(MAX_BARS_AGO);
  });

  it("reads blank as the current bar and refuses what the server would refuse", () => {
    expect(parseBarsAgo("")).toBe(0);
    expect(parseBarsAgo("  ")).toBe(0);
    expect(parseBarsAgo("0")).toBe(0);
    expect(parseBarsAgo("1")).toBe(1);
    expect(parseBarsAgo(" 21 ")).toBe(21);
    expect(parseBarsAgo(String(MAX_BARS_AGO))).toBe(MAX_BARS_AGO);
    for (const bad of [String(MAX_BARS_AGO + 1), "-1", "1.5", "abc", "1e2", "00001"]) expect(parseBarsAgo(bad)).toBeNull();
    expect(barsAgo({})).toBe(0);
    expect(barsAgo({ offset: -2 })).toBe(0);
    expect(barsAgo({ offset: MAX_BARS_AGO + 1 })).toBe(0);
    expect(barsAgoWords(1)).toBe("1 bar ago");
    expect(barsAgoWords(3)).toBe("3 bars ago");
  });

  it("writes [n] before the timeframe, nothing for the current bar, on both sides and in a cross", () => {
    const s = newStage(reg);
    expect(stageText(reg, s)).toBe("RSI(14) > 60");
    const back: IndicatorStage = { ...s, left: withBarsAgo({ ...s.left, tf: "15m" }, 3) };
    expect(stageText(reg, back)).toBe("RSI(14)[3]@15m > 60");
    expect(stageSummary(reg, back)).toBe("RSI(14) on 15m 3 bars ago above 60");
    expect(stageText(reg, { ...back, left: withBarsAgo(back.left, 0) })).toBe("RSI(14)@15m > 60");
    expect("offset" in withBarsAgo(back.left, 0)).toBe(false);
    const cross: IndicatorStage = { ...s, op: "crosses above", left: withBarsAgo(seriesRef(reg, "close"), 1),
      right: { kind: "series", ref: withBarsAgo(seriesRef(reg, "EMA"), 2) } };
    expect(stageText(reg, cross)).toBe("CrossAbove(close[1], EMA(close, 20)[2])");
    expect(stageSummary(reg, cross)).toBe("close 1 bar ago crosses above EMA(close, 20) 2 bars ago");
  });

  it("keeps bars ago when the indicator changes", () => {
    expect(seriesRef(reg, "EMA", "1h", 4)).toMatchObject({ fn: "EMA", tf: "1h", offset: 4 });
    expect("offset" in seriesRef(reg, "EMA")).toBe(false);
  });
});

describe("FN2-b: the text round trip (builder -> server text -> builder)", () => {
  it("opens an offset on either side of a comparison or a cross as the same indicator block", () => {
    const stages = stagesFromServer(reg, [
      item("RSI(14)[3]@15m > 60", { k: "Compare", op: ">", left: call("RSI", [num(14)], 3, "15m"), right: num(60) }),
      item("close < EMA(close, 20)[5]", { k: "Compare", op: "<", left: field("close"), right: call("EMA", [field("close"), num(20)], 5) }),
      item("CrossAbove(close[1], EMA(close, 20)[1])", call("CrossAbove", [field("close", 1), call("EMA", [field("close"), num(20)], 1)])),
      item(`RSI(14)[${MAX_BARS_AGO}] > 50`, { k: "Compare", op: ">", left: call("RSI", [num(14)], MAX_BARS_AGO), right: num(50) }),
    ]);
    expect(stages.map((s) => s.kind)).toEqual(["indicator", "indicator", "indicator", "indicator"]);
    const first = stages[0] as IndicatorStage;
    expect(first.left).toMatchObject({ fn: "RSI", tf: "15m", offset: 3 });
    expect((stages[1] as IndicatorStage).right).toMatchObject({ kind: "series", ref: { fn: "EMA", offset: 5 } });
    expect(screenText(reg, stages)).toBe(
      `RSI(14)[3]@15m > 60 AND close < EMA(close, 20)[5] AND CrossAbove(close[1], EMA(close, 20)[1]) AND RSI(14)[${MAX_BARS_AGO}] > 50`);
  });

  it("round-trips every contract case the builder writes", () => {
    for (const c of contract.cases) {
      const s = { id: "x", enabled: true, ...c.stage } as unknown as Stage;
      expect(stageText(reg, s)).toBe(c.text);
    }
    const withOffset = contract.cases.filter((c) => c.stage.kind === "indicator" && c.text.includes("["));
    expect(withOffset.length).toBeGreaterThanOrEqual(2);
  });

  it("keeps a formula block where the block has no bars-ago control, or the offset is out of range", () => {
    const stages = stagesFromServer(reg, [
      item("Pattern(\"doji\")[1]", call("Pattern", [{ k: "Str", v: "doji" }], 1)),
      item("Rank(PctChange(close, 5)[1]) <= 10", { k: "Compare", op: "<=", left: call("Rank", [call("PctChange", [field("close"), num(5)], 1)]), right: num(10) }),
      item("EMA(close[1], 20) > 1", { k: "Compare", op: ">", left: call("EMA", [field("close", 1), num(20)]), right: num(1) }),
      item(`RSI(14)[${MAX_BARS_AGO + 1}] > 50`, { k: "Compare", op: ">", left: call("RSI", [num(14)], MAX_BARS_AGO + 1), right: num(50) }),
    ]);
    expect(stages.map((s) => s.kind)).toEqual(["formula", "formula", "formula", "formula"]);
  });
});

describe("FN2-b: the control", () => {
  const html = (stage: Stage) => renderToStaticMarkup(
    <StageRow stage={stage} index={0} registry={reg} scanTf="1d" trail={null} total={null}
              onChange={() => undefined} onMove={() => undefined} onDuplicate={() => undefined} onRemove={() => undefined} />);

  it("is blank for the current bar, and 44 px on a phone", () => {
    const out = renderToStaticMarkup(<BarsAgoInline value={0} label="Left side, bars ago" onChange={() => undefined} />);
    expect(out).toContain('aria-label="Left side, bars ago"');
    expect(out).toContain('value=""');
    expect(out).toContain('placeholder="now"');
    expect(out).toMatch(/class="[^"]*\bh-11\b[^"]*\bmd:h-7\b/);
    expect(renderToStaticMarkup(<BarsAgoInline value={1} label="x" onChange={() => undefined} />)).toContain("bar ago");
  });

  it("shows in the block's sentence only when a side reads back; the current bar keeps the sentence as it was", () => {
    const s = newStage(reg);
    expect(html(s)).not.toContain("bars-ago-left-inline");
    const back = html({ ...s, left: withBarsAgo(s.left, 2) });
    expect(back).toContain('data-testid="bars-ago-left-inline"');
    expect(back).toContain('value="2"');
  });
});
