import { describe, expect, it } from "vitest";
import contract from "./builderTexts.json";
import { stagesFromServer, type AstNode, type ServerStage } from "./importStages";
import { blockProblems, formulaText, needsBrackets, newBlock, screenText, stageSummary, stageText, type Registry } from "./model";

const reg = contract.registry as unknown as Registry;
const num = (v: number): AstNode => ({ k: "Num", v });
const field = (name: string, offset = 0, tf: string | null = null): AstNode => ({ k: "Field", name, offset, tf });
const call = (name: string, args: AstNode[], offset = 0, tf: string | null = null): AstNode => ({ k: "Call", name, args, kwargs: {}, offset, tf });
const item = (text: string, ast: AstNode, wrapped = text): ServerStage => ({ text, wrapped, ast });

describe("the formula block (U2)", () => {
  it("brackets a top-level AND / OR so it stays one stage, and nothing else", () => {
    expect(needsBrackets("close > 1 OR high > 2")).toBe(true);
    expect(needsBrackets("close > 1 and high > 2")).toBe(true);
    expect(needsBrackets("(close > 1 OR high > 2)")).toBe(false);
    expect(needsBrackets('Pattern("doji OR x") ')).toBe(false);                       // inside a string
    expect(needsBrackets("NOT (a > 1 AND b > 2)")).toBe(false);
    expect(formulaText("  high > 1 ")).toBe("high > 1");
    expect(formulaText("a > 1 OR b > 1")).toBe("(a > 1 OR b > 1)");
  });

  it("starts empty with a problem on the block, and reads as a formula", () => {
    const f = newBlock(reg, "formula");
    expect(f.kind).toBe("formula");
    expect(Object.values(blockProblems(reg, [f]))[0]).toContain("Type the condition in ScreenQL");
    expect(stageSummary(reg, { ...f, kind: "formula", text: "high > 1" })).toBe("formula: high > 1");
  });
});

describe("opening a screen from the server's stages (U2)", () => {
  it("uses a block form only where it writes back the same text, else a formula block", () => {
    const stages = stagesFromServer(reg, [
      item("RSI(14) > 60", { k: "Compare", op: ">", left: call("RSI", [num(14)]), right: num(60) }),
      item("high > Max(high, 20)[1]", { k: "Compare", op: ">", left: field("high"), right: call("Max", [field("high"), num(20)], 1) }),
      item("CrossAbove(close, EMA(close, 20))", call("CrossAbove", [field("close"), call("EMA", [field("close"), num(20)])])),
      item("close >= 0.75 * Max(high, 250)", { k: "Compare", op: ">=", left: field("close"), right: { k: "Binary", op: "*", left: num(0.75), right: call("Max", [field("high"), num(250)]) } }),
      item("close > 1 OR high > 2", { k: "Logic", op: "ANY", items: [] }, "(close > 1 OR high > 2)"),
      item("Nope(3) > 1", { k: "Compare", op: ">", left: call("Nope", [num(3)]), right: num(1) }),
    ]);
    expect(stages.map((s) => s.kind)).toEqual(["indicator", "formula", "indicator", "formula", "formula", "formula"]);
    expect(screenText(reg, stages)).toBe(
      "RSI(14) > 60 AND high > Max(high, 20)[1] AND CrossAbove(close, EMA(close, 20)) AND close >= 0.75 * Max(high, 250) AND "
      + "(close > 1 OR high > 2) AND Nope(3) > 1");
    expect(new Set(stages.map((s) => s.id)).size).toBe(stages.length);
    expect(stageText(reg, stages[0])).toBe("RSI(14) > 60");
  });
});
