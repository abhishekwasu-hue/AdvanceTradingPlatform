#!/usr/bin/env node
// AUD-G1.6: ESLint over src/ with a per-rule ratchet. Errors always fail. The rules the code was not clean on when
// ESLint was added are warnings (eslint.config.js); their counts are held at BASELINE and may only fall. When a PR
// lowers a count, lower the number here in the same PR (the output says which). Never raise a number.
import { ESLint } from "eslint";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

// Recorded with eslint 10.12.0 / typescript-eslint 8.71.2 when the ratchet was added (101 warnings).
const BASELINE = {
  "@typescript-eslint/no-unused-vars": 54,
  "react-hooks/exhaustive-deps": 30,
  "no-useless-assignment": 7,
  "unused-disable-directive": 7,   // an `eslint-disable` comment that no longer suppresses anything
  "no-control-regex": 2,
  "no-regex-spaces": 1,
};

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const eslint = new ESLint({ cwd: root });
const results = await eslint.lintFiles(["src"]);

const counts = {};
let errors = 0;
for (const file of results) {
  for (const m of file.messages) {
    if (m.severity === 2) {
      errors += 1;
      console.error(`${file.filePath.slice(root.length + 1)}:${m.line}:${m.column} error ${m.message} (${m.ruleId ?? "parse"})`);
      continue;
    }
    const rule = m.ruleId ?? "unused-disable-directive";
    counts[rule] = (counts[rule] ?? 0) + 1;
  }
}

const risen = Object.entries(counts).filter(([rule, n]) => n > (BASELINE[rule] ?? 0));
const lowered = Object.entries(BASELINE).filter(([rule, base]) => (counts[rule] ?? 0) < base);

if (risen.length) {
  const formatter = await eslint.loadFormatter("stylish");
  const warned = new Set(risen.map(([rule]) => rule));
  const shown = results.map((r) => ({ ...r, messages: r.messages.filter((m) => m.severity === 1 && warned.has(m.ruleId ?? "unused-disable-directive")) }));
  console.error(await formatter.format(shown));
  for (const [rule, n] of risen) console.error(`ESLint ratchet: ${rule} rose to ${n} (baseline ${BASELINE[rule] ?? 0}) - fix the new findings.`);
}
for (const [rule, base] of lowered) console.log(`ESLint ratchet: lower BASELINE["${rule}"] in scripts/check-eslint-ratchet.mjs: ${base} -> ${counts[rule] ?? 0}`);

const total = Object.values(counts).reduce((a, b) => a + b, 0);
console.log(`ESLint: ${errors} error(s), ${total} ratcheted warning(s) over ${results.length} files.`);
process.exit(errors || risen.length ? 1 : 0);
