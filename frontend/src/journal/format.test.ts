import ts from "typescript";
import { describe, expect, it } from "vitest";
import {
  COMPONENT_LABELS, SETTING_LABELS, TAG_HELP, TAG_LABELS, chase, countsInScore, isoWeekOf, minutesText, money, parseSettingNumber, rText, settingsProblems,
  shiftWeek, tagLabel,
} from "./format";

describe("journal format helpers", () => {
  it("names IST weeks the ISO way", () => {
    expect(isoWeekOf(new Date("2026-10-05T00:30:00+05:30"))).toBe("2026-W41");      // Monday early morning IST
    expect(isoWeekOf(new Date("2026-10-11T20:00:00Z"))).toBe("2026-W42");           // Monday 01:30 IST
    expect(isoWeekOf(new Date("2027-01-01T06:00:00Z"))).toBe("2026-W53");
    expect(shiftWeek("2026-W41", -1)).toBe("2026-W40");
    expect(shiftWeek("2026-W01", -1)).toBe("2025-W52");
    expect(shiftWeek("2026-W53", 1)).toBe("2027-W01");
    expect(shiftWeek("bad", 1)).toBe("bad");
  });

  it("formats money, R and holding time", () => {
    expect(money(-1234.5)).toBe("-₹1,234.50");
    expect(money(null)).toBe("-");
    expect(rText(1.5)).toBe("+1.50R");
    expect(rText(-0.25)).toBe("-0.25R");
    expect(minutesText(25)).toBe("25 min");
    expect(minutesText(125)).toBe("2 h 5 min");
    expect(minutesText(60 * 50)).toBe("2 d 2 h");
  });

  it("keeps a cleared settings field blank instead of sending 0", () => {
    expect(parseSettingNumber("")).toBeNull();
    expect(parseSettingNumber("  ")).toBeNull();
    expect(parseSettingNumber("abc")).toBeNull();
    expect(parseSettingNumber("0")).toBe(0);
    expect(parseSettingNumber("1.5")).toBe(1.5);
    const labels = { fomo_chase_r: "FOMO chase", "w:plan_adherence": "Plan adherence weight" };
    const ranges: Record<string, [number, number]> = { fomo_chase_r: [0.05, 2] };
    expect(settingsProblems({ fomo_chase_r: "0.25", "w:plan_adherence": "20" }, labels, ranges)).toEqual([]);
    expect(settingsProblems({ fomo_chase_r: "", "w:plan_adherence": "" }, labels, ranges)).toEqual([
      "FOMO chase: enter a number from 0.05 to 2", "Plan adherence weight: enter a number"]);
    expect(settingsProblems({ fomo_chase_r: "5" }, labels, ranges)).toEqual(["FOMO chase: 5 is outside 0.05-2"]);
  });

  it("measures the chase in the trade's own direction", () => {
    expect(chase("LONG", 100, 100.6)).toBe(0.6);
    expect(chase("SHORT", 100, 99.2)).toBe(0.8);
    expect(chase("LONG", undefined, 101)).toBeNull();
  });

  it("counts tags like the backend score does", () => {
    expect(countsInScore("CONFIRMED", false)).toBe(true);
    expect(countsInScore("SUGGESTED", true)).toBe(true);
    expect(countsInScore("SUGGESTED", false)).toBe(false);
    expect(countsInScore("DISMISSED", true)).toBe(false);
    expect(tagLabel("no_stop")).toBe("No stop");
    expect(tagLabel("something_new")).toBe("something new");
  });
});

// Compliance: the journal's words describe; they never advise, recommend or promise (the Copilot's BANNED list plus
// instruction words). Checked over every string literal and JSX text in the page and the journal module.
const BANNED: RegExp[] = [
  /\brecommend(ed|s|ing|ations?)?\b/i, /\bbest\b/i, /\bfor you\b/i, /\bguarantee(d|s)?\b/i, /\bassured\b|\brisk[- ]free\b|\bsure[- ]?shot\b/i,
  /\b(sure|certain|easy|guaranteed) (profit|returns?|gains?)\b|\bdouble your\b/i, /\byou should\b/i, /\byou must\b/i,
];
const RAW = {
  ...import.meta.glob<string>(["./*.ts", "!./*.test.ts"], { query: "?raw", import: "default", eager: true }),
  ...import.meta.glob<string>(["../pages/JournalPage.tsx"], { query: "?raw", import: "default", eager: true }),
  // M62 JN-3: the v2 page parts
  ...import.meta.glob<string>(["./*V2.tsx"], { query: "?raw", import: "default", eager: true }),
};

function literals(file: string, text: string): string[] {
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const out: string[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) out.push(node.text);
    else if (ts.isTemplateExpression(node)) { out.push(node.head.text); node.templateSpans.forEach((s) => out.push(s.literal.text)); }
    else if (ts.isJsxText(node)) out.push(node.getText());
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

describe("journal wording", () => {
  it("has no advice, recommendation or promise in any label or page text", () => {
    const labels = [...Object.values(TAG_LABELS), ...Object.values(TAG_HELP), ...Object.values(COMPONENT_LABELS),
      ...Object.values(SETTING_LABELS).flatMap((s) => [s.label, s.unit ?? ""])];
    expect(Object.keys(RAW).length).toBeGreaterThanOrEqual(4);
    const texts = [...labels, ...Object.entries(RAW).flatMap(([file, text]) => literals(file, text))];
    const bad = texts.filter((t) => BANNED.some((re) => re.test(t)));
    expect(bad).toEqual([]);
  });
});
