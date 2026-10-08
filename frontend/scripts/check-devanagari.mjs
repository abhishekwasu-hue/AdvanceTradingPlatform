#!/usr/bin/env node
// P0.9 / P0.10 (ATP_COPILOT_UI_REDESIGN_PROMPT section 5): the dashboard is English only. Devanagari may appear in the
// frontend source only in the interview namespace - the values of INTERVIEW_MR in i18n/interviewSecondary.ts, the
// muted Marathi lines of the strategy interview - and that namespace may be used only by the interview screens.
// Text the server sends (interview questions, the "Read in Marathi" translation of the acknowledgement and of the data
// consent, AI answers in the user's chosen language) is data, not UI strings, and is not checked here.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(fileURLToPath(new URL(".", import.meta.url)), "..", "src");
const NAMESPACE_FILE = "i18n/interviewSecondary.ts";
const NAMESPACE_USERS = new Set(["components/StrategyInterview.tsx", "pages/AiCopilotPage.tsx"]);   // the interview and its Start card
const DEVANAGARI = /[ऀ-ॿ]/;
const KEY_VALUE = /^\s*[A-Za-z][A-Za-z0-9]*:\s*"[^"]*",?\s*(\/\/.*)?$/;

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) yield* walk(path);
    else if (/\.(ts|tsx)$/.test(name) && !/\.test\.tsx?$/.test(name)) yield path;
  }
}

const problems = [];
for (const file of walk(root)) {
  const rel = relative(root, file).split(sep).join("/");
  const text = readFileSync(file, "utf8");
  if (rel === NAMESPACE_FILE) {
    // Inside the namespace file: Devanagari only as the value of a key of the INTERVIEW_MR object.
    const start = text.indexOf("export const INTERVIEW_MR");
    const end = text.indexOf("} as const", start);
    text.split("\n").forEach((line, i) => {
      if (!DEVANAGARI.test(line)) return;
      const offset = text.split("\n").slice(0, i).join("\n").length;
      const inside = start >= 0 && end > start && offset > start && offset < end;
      if (!inside || !KEY_VALUE.test(line)) problems.push(`${rel}:${i + 1}: Devanagari outside an INTERVIEW_MR key: ${line.trim().slice(0, 100)}`);
    });
    continue;
  }
  if (/i18n\/interviewSecondary/.test(text) && !NAMESPACE_USERS.has(rel)) {
    problems.push(`${rel}: uses the interview namespace, but only the interview screens may (${[...NAMESPACE_USERS].join(", ")})`);
  }
  text.split("\n").forEach((line, i) => {
    if (DEVANAGARI.test(line)) problems.push(`${rel}:${i + 1}: ${line.trim().slice(0, 120)}`);
  });
}
if (problems.length) {
  console.error(`Devanagari outside the interview namespace (${problems.length} problem(s)) - the dashboard UI is English only:`);
  for (const p of problems) console.error("  " + p);
  process.exit(1);
}
console.log(`check-devanagari: OK - Devanagari only in the INTERVIEW_MR keys of ${NAMESPACE_FILE}, used by ${[...NAMESPACE_USERS].join(", ")}`);
