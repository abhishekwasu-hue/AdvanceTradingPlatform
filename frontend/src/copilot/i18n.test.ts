import { describe, expect, it } from "vitest";
import { INTERVIEW_MR } from "../i18n/interviewSecondary";
import { copilotI18n } from "./i18n";
import { COPILOT_EN } from "./locales/en";

/**
 * The Copilot's i18n: every key the code asks for exists in the English bundle, and Devanagari appears only in the
 * interview's second-language namespace (the allowlist) - never in an interface string.
 */
const DEVANAGARI = /[ऀ-ॿ]/;

// The Copilot's source text, read through Vite (no Node APIs: the test files are type-checked with the app).
const RAW = {
  ...import.meta.glob<string>(["./**/*.ts", "./**/*.tsx", "!./**/*.test.ts"], { query: "?raw", import: "default", eager: true }),
  ...import.meta.glob<string>("../pages/AiCopilotPage.tsx", { query: "?raw", import: "default", eager: true }),
};

function sources(): { rel: string; text: string }[] {
  return Object.entries(RAW).map(([path, text]) => ({ rel: path.replace(/^\.\.\//, "").replace(/^\.\//, "copilot/"), text }));
}

function lookup(path: string): unknown {
  return path.split(".").reduce<unknown>((node, part) => (node && typeof node === "object" ? (node as Record<string, unknown>)[part] : undefined), COPILOT_EN);
}

function strings(node: unknown, prefix = ""): [string, string][] {
  if (typeof node === "string") return [[prefix, node]];
  if (node && typeof node === "object") return Object.entries(node).flatMap(([k, v]) => strings(v, prefix ? `${prefix}.${k}` : k));
  return [];
}

describe("Copilot i18n keys", () => {
  const files = sources();

  it("finds the Copilot sources", () => {
    expect(files.length).toBeGreaterThan(20);
  });

  it("has an English string for every literal key the code uses", () => {
    const missing: string[] = [];
    for (const f of files) {
      for (const m of f.text.matchAll(/\bt\(\s*"([a-zA-Z][\w.-]*)"/g)) {
        const key = m[1];
        const v = lookup(key) ?? lookup(`${key}_other`) ?? lookup(`${key}_one`);
        if (typeof v !== "string") missing.push(`${f.rel}: ${key}`);
      }
    }
    expect(missing).toEqual([]);
  });

  it("has the group behind every templated key (t(`pulse.regime.${kind}`))", () => {
    const missing: string[] = [];
    for (const f of files) {
      for (const m of f.text.matchAll(/\bt\(\s*`([a-zA-Z][\w.-]*)\.\$\{/g)) {
        const group = lookup(m[1]);
        if (!group || typeof group !== "object") missing.push(`${f.rel}: ${m[1]}`);
      }
    }
    expect(missing).toEqual([]);
  });

  it("names all seven tabs, every regime, verdict and proposal action", () => {
    for (const tab of ["market-pulse", "strategy-lab", "idea-builder", "ask", "watchtower", "news-radar", "coach"]) expect(lookup(`tabs.${tab}`)).toBeTypeOf("string");
    for (const k of ["TREND_UP", "TREND_DOWN", "RANGE", "VOLATILE", "UNKNOWN"]) expect(lookup(`pulse.regime.${k}`)).toBeTypeOf("string");
    for (const k of ["robust", "overfit", "weak", "insufficient", "sample", "thin", "untested"]) expect(lookup(`lab.verdict.${k}`)).toBeTypeOf("string");
    for (const k of ["PAUSE_DEPLOYMENT", "EXIT_POSITION", "REDUCE_RISK", "REVIEW_STRATEGY"]) expect(lookup(`watch.action.${k}`)).toBeTypeOf("string");
  });

  it("has a second-language line for every interview key the Idea Builder asks for", () => {
    const idea = files.find((f) => f.rel.endsWith("IdeaBuilderTab.tsx"))!;
    const keys = [...idea.text.matchAll(/\bline\("(\w+)"\)/g)].map((m) => m[1]);
    expect(keys.length).toBeGreaterThan(20);
    for (const k of keys) expect(INTERVIEW_MR, k).toHaveProperty(k);
  });

  it("interpolates with the i18next syntax", () => {
    const t = copilotI18n.getFixedT("en", "copilot");
    expect(t("idea.questionOf", { n: 2, total: 7 })).toBe("Question 2 of 7");
    expect(t("tabs.badge.watchtower", { count: 1 })).toBe("1 proposal waiting for your decision");
    expect(t("tabs.badge.watchtower", { count: 3 })).toBe("3 proposals waiting for your decision");
  });
});

describe("Devanagari only in the interview allowlist", () => {
  it("never appears in an interface string", () => {
    const bad = strings(COPILOT_EN).filter(([, v]) => DEVANAGARI.test(v)).map(([k]) => k);
    expect(bad).toEqual([]);
  });

  it("appears in the loaded resources only under the interview namespace", () => {
    const store = copilotI18n.store.data as Record<string, Record<string, unknown>>;
    const offenders: string[] = [];
    for (const [lng, namespaces] of Object.entries(store)) {
      for (const [ns, bundle] of Object.entries(namespaces)) {
        for (const [key, value] of strings(bundle)) {
          if (DEVANAGARI.test(value) && ns !== "interview") offenders.push(`${lng}/${ns}:${key}`);
        }
      }
    }
    expect(offenders).toEqual([]);
    expect(Object.values(INTERVIEW_MR).some((v) => DEVANAGARI.test(v))).toBe(true);   // the allowlist itself is Marathi
  });

  it("is not typed into any Copilot source file", () => {
    const bad = sources().filter((f) => DEVANAGARI.test(f.text)).map((f) => f.rel);
    expect(bad).toEqual([]);
  });

  it("serves the second-language line from the interview namespace", () => {
    expect(copilotI18n.getFixedT("mr", "interview")("start")).toBe(INTERVIEW_MR.start);
  });
});
