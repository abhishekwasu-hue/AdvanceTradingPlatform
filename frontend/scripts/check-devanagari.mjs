#!/usr/bin/env node
// P0.9 (ATP_COPILOT_UI_REDESIGN_PROMPT section 5): the dashboard is English only. Devanagari may appear in the
// frontend source only in the allowlisted interview file (the muted secondary lines under the strategy-interview
// questions). Text the server sends - interview questions, the "Read in Marathi" translation of the acknowledgement
// and of the data consent, AI answers in the user's chosen language - is data, not UI strings, and is not checked here.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(fileURLToPath(new URL(".", import.meta.url)), "..", "src");
const ALLOWLIST = new Set(["i18n/interviewSecondary.ts"]);
const DEVANAGARI = /[ऀ-ॿ]/;

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* walk(path);
    else if (/\.(ts|tsx)$/.test(name)) yield path;
  }
}

const problems = [];
for (const file of walk(root)) {
  const rel = relative(root, file).split(sep).join("/");
  if (ALLOWLIST.has(rel)) continue;
  readFileSync(file, "utf8").split("\n").forEach((line, i) => {
    if (DEVANAGARI.test(line)) problems.push(`${rel}:${i + 1}: ${line.trim().slice(0, 120)}`);
  });
}
if (problems.length) {
  console.error(`Devanagari outside the interview allowlist (${problems.length} line(s)) - the dashboard UI is English only:`);
  for (const p of problems) console.error("  " + p);
  process.exit(1);
}
console.log("check-devanagari: OK - Devanagari only in " + [...ALLOWLIST].join(", "));
