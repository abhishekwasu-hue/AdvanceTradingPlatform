import { describe, expect, it } from "vitest";
import { GLOSSARY, glossary } from "./glossary";

/** UI2-0: the glossary is plain English for traders (UI v2 proposal §3 "Developer wording removed", §9 rule 1). */
const DEVELOPER_WORDS = /\b(api\/|endpoint|registry|adapter|flag|backend|payload|token_|null|undefined|json|http|503|404)\b/i;

describe("UI2-0 glossary", () => {
  const entries = Object.entries(GLOSSARY);

  it("holds the terms the proposal names", () => {
    for (const key of ["pcr", "max-pain", "vwap", "reward-risk", "lot", "mis", "cnc", "totp", "static-ip"] as const) {
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

  it("shows each term once", () => {
    const terms = entries.map(([, e]) => e.term.toLowerCase());
    expect(new Set(terms).size).toBe(terms.length);
  });
});
