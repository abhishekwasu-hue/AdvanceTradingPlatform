import { describe, expect, it } from "vitest";
import { GLOSSARY, glossary } from "./glossary";

/** UI2-0: the glossary is plain English for traders (UI v2 proposal §3 "Developer wording removed", §9 rule 1). */
const DEVELOPER_WORDS = /\b(api\/|endpoint|registry|adapter|flag|backend|payload|token_|null|undefined|json|http|503|404)\b/i;

describe("UI2-0 glossary", () => {
  const entries = Object.entries(GLOSSARY);

  it("holds the terms the proposal names", () => {
    for (const key of ["pcr", "max-pain", "vwap", "reward-risk", "lot", "mis", "cnc", "totp", "static-ip", "nrml", "sl", "sl-m", "oi-change", "dte"] as const) {
      expect(glossary(key).definition.length).toBeGreaterThan(0);
    }
  });

  it("every definition is one or two short plain sentences", () => {
    for (const [key, e] of entries) {
      expect(e.term.trim(), key).toBe(e.term);
      expect(e.definition, key).toMatch(/^[A-Z].*\.$/);
      expect(e.definition.length, key).toBeLessThanOrEqual(160);
      expect(e.definition.split(/(?<=\.)\s+(?=[A-Z])/).length, key).toBeLessThanOrEqual(2);
      expect(e.definition, key).not.toMatch(DEVELOPER_WORDS);
    }
  });

  it("gives advice nowhere", () => {
    for (const [key, e] of entries) expect(e.definition, key).not.toMatch(/\b(you should|buy now|sell now|guaranteed|recommend)\b/i);
  });

  it("M63 I-7 / CLAUDE.md §3: no definition carries buy / sell / stop-loss wording; target only on the user's own setting terms", () => {
    // "Target" and "R:R" name the user's own exit settings (his dashboard values); every other definition stays clear of them.
    const OWN_SETTING_TERMS = new Set(["target", "reward-risk"]);
    for (const [key, e] of entries) {
      expect(e.definition.trim().length, key).toBeGreaterThan(0);
      expect(e.definition, key).not.toMatch(/\b(buy|buys|buying|bought|buyer|sell|sells|selling|sold|seller|stop[- ]?loss|SL)\b/i);
      if (!OWN_SETTING_TERMS.has(key)) expect(e.definition, key).not.toMatch(/\btargets?\b/i);
    }
  });

  it("shows each term once", () => {
    const terms = entries.map(([, e]) => e.term.toLowerCase());
    expect(new Set(terms).size).toBe(terms.length);
  });
});
