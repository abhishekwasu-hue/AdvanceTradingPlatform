// @vitest-environment node
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";
import ts from "typescript";
import { describe, expect, it } from "vitest";
import { BANNED_UI_WORDS, USER_PAGE_ROOTS, ALLOWED, isClassList, uiStrings, type Allowed } from "./bannedUiWords";

/**
 * UX3 fix 10 (UX audit §8, Master Order 3 §3 "a CI guard: a list of banned UI words"): the developer terms, internal
 * labels, enum codes and sample-data words the audit found must not appear in what a user page shows. This test reads
 * the source of every user page (USER_PAGE_ROOTS: the v2 pages and their parts; the admin console and the old pages
 * that only the flag-off shell shows are outside it), takes the strings a user can see (JSX text, string attributes such
 * as placeholder / title / aria-label, and every other string literal that is a sentence, not a class list) and fails on
 * any banned word. A string that may stay (inside an "Advanced" fold, the admin console's own parts) is listed in
 * ALLOWED with its file and the reason.
 */

const SRC = join(__dirname, "..");

function filesUnder(dir: string): string[] {
  const out: string[] = [];
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) out.push(...filesUnder(path));
    else if (/\.tsx?$/.test(name) && !/\.(test|spec|stories)\.tsx?$/.test(name) && !/fixture/i.test(name)) out.push(path);
  }
  return out;
}

function userPageFiles(): string[] {
  return USER_PAGE_ROOTS.flatMap((root) => {
    const path = join(SRC, root);
    return statSync(path).isDirectory() ? filesUnder(path) : [path];
  }).sort();
}

interface Hit { file: string; line: number; word: string; text: string }

function hitsIn(file: string): Hit[] {
  const rel = relative(SRC, file).split("\\").join("/");
  const source = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, file.endsWith("x") ? ts.ScriptKind.TSX : ts.ScriptKind.TS);
  const hits: Hit[] = [];
  for (const { text, pos } of uiStrings(source)) {
    for (const word of BANNED_UI_WORDS) {
      if (!word.pattern.test(text)) continue;
      if (ALLOWED.some((a: Allowed) => a.file === rel && a.word === word.name && (!a.text || text.includes(a.text)))) continue;
      hits.push({ file: rel, line: source.getLineAndCharacterOfPosition(pos).line + 1, word: word.name, text: text.slice(0, 120) });
    }
  }
  return hits;
}

// It parses every user page with the TypeScript compiler: seconds of CPU, more on a busy CI runner.
describe("banned UI words on user pages (UX audit §8)", { timeout: 30_000 }, () => {
  it("no user page shows a banned word outside Advanced / the admin console", () => {
    const hits = userPageFiles().flatMap(hitsIn);
    expect(hits.map((h) => `${h.file}:${h.line} [${h.word}] ${h.text}`)).toEqual([]);
  });

  it("every allowed exception still matches something (no stale entries)", () => {
    const files = new Map(userPageFiles().map((f) => [relative(SRC, f).split("\\").join("/"), f]));
    const stale = ALLOWED.filter((a) => {
      const file = files.get(a.file);
      if (!file) return true;
      const source = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
      const word = BANNED_UI_WORDS.find((w) => w.name === a.word);
      return !word || !uiStrings(source).some(({ text }) => word.pattern.test(text) && (!a.text || text.includes(a.text)));
    });
    expect(stale.map((a) => `${a.file} [${a.word}]`)).toEqual([]);
  });

  it("finds a banned word in made-up page source (the guard works)", () => {
    const fake = ts.createSourceFile("x.tsx", `export const X = () => <p title="Bot token from BotFather">Deterministic sample OHLCV from the backend</p>;`,
      ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    const found = uiStrings(fake).flatMap(({ text }) => BANNED_UI_WORDS.filter((w) => w.pattern.test(text)).map((w) => w.name));
    expect(found).toEqual(expect.arrayContaining(["BotFather", "Deterministic", "OHLCV", "backend"]));
  });

  it("does not read class lists, imports or test ids as text", () => {
    const fake = ts.createSourceFile("x.tsx", `import a from "../backend/worker";
      const BTN = "rounded-control bg-brand px-space-4 md:min-h-9 hover:bg-brand-strong";
      export const X = () => <div className="worker-panel text-fg" data-testid="backend worker">{a}{BTN}</div>;`,
    ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
    expect(uiStrings(fake).map((s) => s.text)).toEqual([]);
    expect(isClassList("rounded-control bg-brand px-space-4 md:min-h-9")).toBe(true);
    expect(isClassList("Prices from your broker")).toBe(false);
  });

  it("the signal-language pattern catches single prices in signal text", () => {
    const word = BANNED_UI_WORDS.find((w) => w.name === "Entry · Stop · Target prices")!;
    expect(word.pattern.test("Entry 24,790.00 · Stop 24,720.00 · Target 24,930.00")).toBe(true);
    expect(word.pattern.test("Entry ${formatPrice(x)} · Stop ${formatPrice(y)}")).toBe(true);
    expect(word.pattern.test("The pullback reached a seller area")).toBe(false);
    const one = BANNED_UI_WORDS.find((w) => w.name === "Stop / Target price")!;
    expect(one.pattern.test("Stop ${formatPrice(num(e.stop_loss))}")).toBe(true);   // one price per string (the old Dashboard row)
    expect(one.pattern.test("Target ₹24,930")).toBe(true);
    expect(one.pattern.test("Stop the bot")).toBe(false);
    expect(one.pattern.test("Stop ${name}")).toBe(false);                            // "Stop <bot name>": a button, not a price
  });
});
