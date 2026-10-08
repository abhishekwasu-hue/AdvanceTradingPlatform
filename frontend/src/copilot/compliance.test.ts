import ts from "typescript";
import { describe, expect, it } from "vitest";
import { COPILOT_EN } from "./locales/en";

/**
 * P0.8 compliance as a lint: no "Recommended", "Best", "for you", match %, guarantees or other profit claims in any
 * Copilot interface string or in the text typed into a Copilot source file. A disclaimer that says what the Copilot is
 * NOT ("not a recommendation") is allowed.
 */
export const BANNED: { name: string; re: RegExp }[] = [
  { name: "recommended", re: /\brecommend(ed|s|ing)?\b/i },
  { name: "recommendation (unless negated)", re: /(?<!\b(?:not an?|no|none of them is an?|is not an?|this is not an?)\s)\brecommendations?\b/i },
  { name: "best", re: /\bbest\b/i },
  { name: "for you", re: /\bfor you\b/i },
  { name: "match %", re: /\bmatch\s*%|%\s*match\b|\bmatch(ing)? score\b/i },
  { name: "guarantee", re: /\bguarantee(d|s)?\b/i },
  { name: "assured / risk-free / sure-shot", re: /\bassured\b|\brisk[- ]free\b|\bsure[- ]?shot\b/i },
  { name: "profit claim", re: /\b(sure|certain|easy|guaranteed) (profit|returns?|gains?)\b|\bdouble your\b/i },
];

export function violations(text: string): string[] {
  return BANNED.filter((b) => b.re.test(text)).map((b) => b.name);
}

const RAW = {
  ...import.meta.glob<string>(["./**/*.ts", "./**/*.tsx", "!./**/*.test.ts"], { query: "?raw", import: "default", eager: true }),
  ...import.meta.glob<string>(["../pages/AiCopilotPage.tsx", "../components/TradeCoach.tsx"], { query: "?raw", import: "default", eager: true }),
};

function strings(node: unknown, prefix = ""): [string, string][] {
  if (typeof node === "string") return [[prefix, node]];
  if (node && typeof node === "object") return Object.entries(node).flatMap(([k, v]) => strings(v, prefix ? `${prefix}.${k}` : k));
  return [];
}

/** The text a source file can show: string literals, template text and JSX text (not identifiers or comments - an
 * API field such as `option.recommended` is code, not words on the screen). Parsed with the TypeScript compiler. */
function literals(file: string, text: string): string[] {
  const sf = ts.createSourceFile(file, text, ts.ScriptTarget.Latest, true, file.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const out: string[] = [];
  const visit = (node: ts.Node) => {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node) || ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node)) out.push(node.text);
    else if (ts.isJsxText(node) && node.text.trim()) out.push(node.text.trim());
    ts.forEachChild(node, visit);
  };
  visit(sf);
  return out;
}

describe("compliance lint on the Copilot's words", () => {
  it("catches the banned phrases and lets a negated disclaimer through", () => {
    expect(violations("Recommended for you")).toEqual(expect.arrayContaining(["recommended", "for you"]));
    expect(violations("The best strategy")).toEqual(["best"]);
    expect(violations("92% match")).toEqual(["match %"]);
    expect(violations("Guaranteed returns")).toContain("guarantee");
    expect(violations("You choose the template; this is not a recommendation.")).toEqual([]);
    expect(violations("A recommendation engine")).toEqual(["recommendation (unless negated)"]);
  });

  it("finds none in the English interface strings", () => {
    const found = strings(COPILOT_EN).flatMap(([k, v]) => violations(v).map((n) => `${k}: ${n}`));
    expect(found).toEqual([]);
  });

  it("finds none in the text of the Copilot source files", () => {
    const found = Object.entries(RAW).flatMap(([f, text]) => literals(f, text).flatMap((str) => violations(str).map((n) => `${f}: "${str.slice(0, 60)}" (${n})`)));
    expect(found).toEqual([]);
  });
});
