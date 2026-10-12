import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { LearnContent } from "../pages/LearnPage";
import { LEARN_EN } from "./locales/learn.en";
import { CONCEPTS, CONCEPT_LINKS, GLOSSARY, OFFICIAL_LINKS, conceptFromHash, isOfficial } from "./learn";

/**
 * N2-10: the method library is education only.
 * - Every string passes the same advice check as the portfolio risk text (N1 F3 `risk_text.has_advice`): the English
 *   words as whole words, case-insensitive, and the Marathi words as written.
 * - It passes a performance-claim check too.
 * - It is English only.
 * - Every link points to an official NSE, SEBI or CBDT site.
 */
const ADVICE_WORDS = ["recommend", "advice", "advise", "should", "must", "buy", "sell", "reduce", "cut your", "add more", "hedge now",
  "guarantee", "best", "safe bet", "target price"];
const ADVICE_WORDS_MR = ["खरेदी करा", "विका", "विकून", "कमी करा", "पाहिजे", "हवे", "सल्ला", "विकत घ्या", "घ्या", "करावे"];
const ADVICE_EN = new RegExp(`\\b(${ADVICE_WORDS.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})\\b`, "i");
const hasAdvice = (line: string) => ADVICE_EN.test(line) || ADVICE_WORDS_MR.some((w) => line.includes(w));
const CLAIMS = [/\bguarantee/i, /\bassured\b/i, /\brisk[- ]free\b/i, /\bsure[- ]?shot\b/i, /\bdouble your\b/i, /\bconsistent(ly)? profit/i,
  /\b(high|winning) win rate\b/i, /\d+\s*%\s*(win|accuracy|success|returns?)/i, /\bnever lose/i, /\balways (works|wins|profit)/i,
  /\byou will (earn|make|profit|win)\b/i, /\bmake money\b/i, /\bfinancial freedom\b/i, /\bproven (strategy|system|method)\b/i];
const DEVANAGARI = /[ऀ-ॿ]/;

function strings(node: unknown, prefix = ""): [string, string][] {
  if (typeof node === "string") return [[prefix, node]];
  if (node && typeof node === "object") return Object.entries(node).flatMap(([k, v]) => strings(v, prefix ? `${prefix}.${k}` : k));
  return [];
}
const lookup = (path: string): unknown => path.split(".").reduce<unknown>((n, k) => (n && typeof n === "object" ? (n as Record<string, unknown>)[k] : undefined), LEARN_EN);
const RAW = import.meta.glob<string>(["./learn.ts", "./locales/learn.en.ts", "../pages/LearnPage.tsx"], { query: "?raw", import: "default", eager: true });
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");

describe("method library wording (N2-10)", () => {
  it("the check itself catches advice in either language and a claim", () => {
    for (const bad of ["You should buy here.", "Best entry", "हे विकत घ्या", "sell now", "A proven method with 80% win rate"]) {
      expect(hasAdvice(bad) || CLAIMS.some((re) => re.test(bad))).toBe(true);
    }
    expect(hasAdvice("A loss at the stop is -1 R.")).toBe(false);
  });

  it("has no advice, no performance claim and no Devanagari in any string", () => {
    const problems: string[] = [];
    for (const [key, value] of strings(LEARN_EN)) {
      if (hasAdvice(value)) problems.push(`${key}: advice word`);
      for (const re of CLAIMS) if (re.test(value)) problems.push(`${key}: claim ${re}`);
      if (DEVANAGARI.test(value)) problems.push(`${key}: Devanagari`);
    }
    expect(problems).toEqual([]);
  });

  it("has every string the page asks for", () => {
    const missing: string[] = [];
    for (const c of CONCEPTS) {
      for (const k of ["title", "summary", "body.p1", "body.p2", "body.p3", "onChart", "checklist"]) {
        if (typeof lookup(`concepts.${c}.${k}`) !== "string") missing.push(`concepts.${c}.${k}`);
      }
      for (const tag of CONCEPT_LINKS[c].tags) if (typeof lookup(`tags.${tag}`) !== "string") missing.push(`tags.${tag}`);
    }
    for (const g of GLOSSARY) for (const k of ["term", "def"]) if (typeof lookup(`terms.${g}.${k}`) !== "string") missing.push(`terms.${g}.${k}`);
    for (const l of OFFICIAL_LINKS) if (typeof lookup(`links.${l.key}`) !== "string") missing.push(`links.${l.key}`);
    for (const [rel, src] of Object.entries(RAW)) {
      for (const m of src.matchAll(/\bt\(\s*"([a-zA-Z][\w.-]*)"/g)) if (typeof lookup(m[1]) !== "string") missing.push(`${rel}: ${m[1]}`);
    }
    expect(missing).toEqual([]);
  });
});

describe("links (N2-10)", () => {
  it("allows only official NSE, SEBI and CBDT sites over https", () => {
    for (const ok of ["https://investor.sebi.gov.in/", "https://www.sebi.gov.in/x", "https://www.nseindia.com/", "https://www.incometaxindia.gov.in/"]) {
      expect(isOfficial(ok)).toBe(true);
    }
    for (const bad of ["http://www.sebi.gov.in/", "https://sebi.gov.in.example.com/", "https://www.nseindia.com.evil.io/", "https://example.com/?u=sebi.gov.in",
                       "javascript:alert(1)", "not a url"]) {
      expect(isOfficial(bad)).toBe(false);
    }
  });

  it("every URL written in the education sources is official", () => {
    const urls = Object.values(RAW).flatMap((src) => [...src.matchAll(/https?:\/\/[^\s"'`)<>]+/g)].map((m) => m[0]));
    expect(urls.length).toBeGreaterThan(0);
    expect(urls.filter((u) => !isOfficial(u))).toEqual([]);
  });
});

describe("LearnContent (N2-10)", () => {
  it("shows the risk note, the five steps, one concept, the glossary and the official links", () => {
    const html = renderToStaticMarkup(<LearnContent initial="pullback" />);
    const words = text(html);
    expect(words).toContain("Analysis of Profit and Loss of Individual Traders dealing in Equity F&O Segment");
    expect(words).toContain("9 out of 10 individual traders in the equity F&O segment incurred net losses");
    expect(html).toContain('href="https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&amp;sid=4&amp;ssid=81&amp;smid=109"');
    for (const c of CONCEPTS) expect(words).toContain(LEARN_EN.concepts[c].title);
    expect(html).toContain('data-testid="concept-pullback"');
    expect(words).toContain("The pullback is the wait");
    expect(words).toContain("Checklist item Pullback confirmed");
    expect(words).toContain("Entry chasing beyond the plan's entry");
    expect(words).toContain("R multiple");
    expect(html).toContain('href="https://investor.sebi.gov.in/" target="_blank" rel="noopener noreferrer"');
    expect(conceptFromHash("#learn-rr")).toBe("rr");
    expect(conceptFromHash("#nonsense")).toBe("trend");
  });
});
