import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RowDetailsBody } from "../optionChain/RowDetailsV2";
import type { ChainLeg, ChainRow } from "../optionChain/types";
import { GLOSSARY, type GlossaryKey } from "./glossary";

/**
 * M63 I-7 "(?) on jargon" (M49): on the pages fixed so far, every trading term the page shows carries the one help
 * mark (TermHint, the glossary's "?") - named by its glossary key, either directly (`term="pcr"`) or through a
 * shared part's `hint` / `term` prop. A source scan, so a later edit that drops a mark fails here.
 */
const PAGES: Record<string, GlossaryKey[]> = {
  "src/fno/FuturesChainV2.tsx": ["ltp", "oi", "basis"],
  "src/fno/CockpitV2.tsx": ["theta", "delta", "gamma", "vega"],
  "src/fno/ChainActionSheetV2.tsx": ["ltp"],
  "src/optionChain/RowDetailsV2.tsx": ["ltp", "oi", "iv", "delta", "gamma", "theta", "vega"],
  "src/optionChain/ChainTableV2.tsx": ["ltp", "oi", "iv", "delta", "gamma", "theta", "vega"],
  "src/progression/ExperienceCard.tsx": ["drawdown"],
  "src/progression/LadderEvidence.tsx": ["win-rate", "expectancy", "profit-factor", "kelly", "risk-of-ruin"],
  "src/strategyFlow/StrategyFlowV2.tsx": ["atr", "r-multiple", "reward-risk", "win-rate", "drawdown", "profit-factor", "expectancy"],
  "src/terminal/ContextBarV2.tsx": ["india-vix", "pcr", "atm"],
  "src/dashboard/BlocksV2.tsx": ["ltp"],
};

const marked = (src: string, key: string) => {
  const q = key.replace(/[-]/g, "\\-");
  // term="x" / hint="x" (a prop), term: "x" / hint: "x" (a row), x: "x" (a column map), or [label, value, "x"] (a row tuple).
  return new RegExp(`(\\b(term|hint)\\s*[=:]\\s*"${q}")|(\\b${q}: "${q}")|(, "${q}"\\])`).test(src);
};

describe("M63 I-7 (?) on jargon", () => {
  for (const [file, keys] of Object.entries(PAGES)) {
    it(`${file} marks ${keys.join(", ")}`, () => {
      const src = readFileSync(file, "utf8");
      expect(src, file).toMatch(/TermHint|\bhint\b/);
      for (const key of keys) {
        expect(GLOSSARY[key].definition.length, key).toBeGreaterThan(0);
        expect(marked(src, key), `${file}: ${key}`).toBe(true);
      }
    });
  }

  it("a strike's details show the (?) beside LTP, OI, IV and each Greek", () => {
    const leg = { ltp: 100, oi: 1000, oi_change: 10, volume: 5, iv: 12, bid: 99, ask: 101, delta: 0.5, gamma: 0.01, theta: -2, vega: 3 } as unknown as ChainLeg;
    const row = { strike: 22800, call: leg, put: leg } as unknown as ChainRow;
    const html = renderToStaticMarkup(<RowDetailsBody row={row} />);
    for (const term of ["LTP", "OI", "IV", "Delta", "Gamma", "Theta", "Vega"]) expect(html).toContain(`aria-label="What is ${term}?"`);
    expect(html).not.toContain("What is Bid?");
  });
});
