import { describe, expect, it } from "vitest";
import { PROGRESSION_EN } from "./locales/en";

/**
 * N2 i18n: every key the progression sources ask for exists in the English bundle; no Devanagari; and no advice or
 * profit-claim wording in any string (P1: information, never advice).
 */
const DEVANAGARI = /[ऀ-ॿ]/;
const BANNED: { name: string; re: RegExp }[] = [
  { name: "advice verb", re: /\b(should|must|ought to|consider|avoid|recommend\w*|suggest\w*|advis\w*|make sure)\b/i },
  { name: "trade instruction", re: /\b(buy|sell|go long|go short|size up|size down|increase (your |the )?size|reduce (your |the )?size)\b/i },
  { name: "best / for you", re: /\bbest\b|\bfor you\b/i },
  { name: "guarantee / profit claim", re: /\bguarantee(d|s)?\b|\brisk[- ]free\b|\bsure[- ]?shot\b|\b(sure|easy) (profit|returns?)\b/i },
  { name: "future advice", re: /\b(next week|tomorrow|going forward|from now on|next time)\b/i },
];

const RAW = import.meta.glob<string>(["./**/*.ts", "./**/*.tsx", "!./**/*.test.ts", "!./**/*.test.tsx"],
                                     { query: "?raw", import: "default", eager: true });

function lookup(path: string): unknown {
  return path.split(".").reduce<unknown>((node, part) => (node && typeof node === "object" ? (node as Record<string, unknown>)[part] : undefined), PROGRESSION_EN);
}

function strings(node: unknown, prefix = ""): [string, string][] {
  if (typeof node === "string") return [[prefix, node]];
  if (node && typeof node === "object") return Object.entries(node).flatMap(([k, v]) => strings(v, prefix ? `${prefix}.${k}` : k));
  return [];
}

describe("progression i18n (N2)", () => {
  const files = Object.entries(RAW);

  it("finds the sources", () => {
    expect(files.length).toBeGreaterThanOrEqual(4);
  });

  it("has an English string for every literal key the code uses", () => {
    const missing: string[] = [];
    for (const [rel, text] of files) {
      for (const m of text.matchAll(/\bt\(\s*"([a-zA-Z][\w.-]*)"/g)) {
        const v = lookup(m[1]) ?? lookup(`${m[1]}_other`) ?? lookup(`${m[1]}_one`);   // i18next plural keys
        if (typeof v !== "string") missing.push(`${rel}: ${m[1]}`);
      }
      for (const m of text.matchAll(/\bt\(\s*`([a-zA-Z][\w.-]*)\.\$\{/g)) {
        const group = lookup(m[1]);
        if (!group || typeof group !== "object") missing.push(`${rel}: ${m[1]}.*`);
      }
    }
    expect(missing).toEqual([]);
  });

  it("has every status the server sends for the templated keys", () => {
    for (const s of ["ok", "negative", "no_losses", "no_wins"]) expect(typeof lookup(`evidence.kelly.${s}`)).toBe("string");
    for (const s of ["ok", "certain", "no_losses"]) expect(typeof lookup(`evidence.ruin.${s}`)).toBe("string");
  });

  it("is English only, with no advice or profit-claim wording", () => {
    const problems: string[] = [];
    for (const [key, value] of strings(PROGRESSION_EN)) {
      if (DEVANAGARI.test(value)) problems.push(`${key}: Devanagari`);
      for (const b of BANNED) if (b.re.test(value)) problems.push(`${key}: ${b.name}`);
    }
    expect(problems).toEqual([]);
  });
});
