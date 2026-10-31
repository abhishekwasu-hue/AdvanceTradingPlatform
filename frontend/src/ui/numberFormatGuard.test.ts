import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * M63 I-7 guard (one number formatter, M61 OI-1): numbers on screen go through src/ui/format.ts (formatNumber,
 * formatINR, formatSignedINR, formatPrice, formatPct, formatCompactIN, ...). It counts the ad-hoc number formatting
 * left in app source (tests, stories and fixtures excluded; the formatter module itself is allowed):
 * `.toFixed(`, `.toLocaleString(` and `Intl.NumberFormat`.
 * Some of the counted calls are not on-screen text (an SVG path's coordinates, a rounding inside a calculation, a
 * form input's value) and stay; the count is a ratchet, not a ban.
 *
 * BASELINE is a ratchet: the count may not grow. When a pass moves more call sites onto the formatter, lower BASELINE
 * to the new count printed by this test. (Only "may not grow" is enforced, so a parallel branch that removes some
 * calls never breaks this test after a merge.)
 */
const BASELINE = 66;

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const SRC = join(ROOT, "src");
const ALLOWED = new Set(["src/ui/format.ts"]);

function files(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) files(full, out);
    else if (/\.tsx?$/.test(name) && !/\.(test|spec|stories)\.tsx?$/.test(name) && !/fixture/i.test(name)) out.push(full);
  }
  return out;
}

const AD_HOC = /\.toFixed\(|\.toLocaleString\(|Intl\.NumberFormat/g;

/** The ad-hoc number-formatting calls in one source text, with their line numbers. */
function adHocFormatting(source: string): { line: number; text: string }[] {
  const out: { line: number; text: string }[] = [];
  source.split("\n").forEach((text, i) => {
    const n = text.match(AD_HOC)?.length ?? 0;
    for (let k = 0; k < n; k += 1) out.push({ line: i + 1, text: text.trim().slice(0, 100) });
  });
  return out;
}

describe("number formatter guard (M63 I-7)", () => {
  it("finds the shapes it is meant to find", () => {
    expect(adHocFormatting("const a = v.toFixed(2);")).toHaveLength(1);
    expect(adHocFormatting("x.toLocaleString(\"en-IN\") + y.toLocaleString()")).toHaveLength(2);
    expect(adHocFormatting("new Intl.NumberFormat(\"en-IN\").format(v)")).toHaveLength(1);
    expect(adHocFormatting("formatNumber(v, 2); formatINR(v, { paise: true })")).toHaveLength(0);
    expect(adHocFormatting("d.toLocaleDateString(\"en-GB\")")).toHaveLength(0);
  });

  it("the ad-hoc number formatting in app source does not grow", () => {
    const found = files(SRC)
      .map((f) => relative(ROOT, f).split("\\").join("/"))
      .filter((f) => !ALLOWED.has(f))
      .flatMap((f) => adHocFormatting(readFileSync(join(ROOT, f), "utf8")).map((c) => `${f}:${c.line} ${c.text}`));
    if (found.length !== BASELINE) console.log(`ad-hoc number formatting: ${found.length} (BASELINE ${BASELINE})`);
    expect(found.length, `ad-hoc number formatting grew past ${BASELINE} - use src/ui/format.ts:\n${found.join("\n")}`)
      .toBeLessThanOrEqual(BASELINE);
  });
});
