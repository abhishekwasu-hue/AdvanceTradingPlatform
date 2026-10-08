#!/usr/bin/env node
// P1.1: the JavaScript a first visit downloads before anything is drawn - the entry script plus every chunk the
// built index.html preloads - must stay within the budget (gzip). Page chunks and the chart library load on
// demand and are not counted. Run after `npm run build`; CI fails the frontend job when the budget is exceeded.
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const BUDGET_KB = Number(process.env.INITIAL_JS_BUDGET_KB ?? 300);
const dist = join(dirname(fileURLToPath(import.meta.url)), "..", "dist");

let html;
try {
  html = readFileSync(join(dist, "index.html"), "utf8");
} catch {
  console.error("dist/index.html not found - run `npm run build` first.");
  process.exit(2);
}

const files = new Set();
for (const m of html.matchAll(/<script[^>]*type="module"[^>]*src="([^"]+)"/g)) files.add(m[1]);
for (const m of html.matchAll(/<link[^>]*rel="modulepreload"[^>]*href="([^"]+)"/g)) files.add(m[1]);
if (files.size === 0) {
  console.error("No entry script found in dist/index.html.");
  process.exit(2);
}

let total = 0;
const rows = [];
for (const f of files) {
  const body = readFileSync(join(dist, f.replace(/^\//, "")));
  const gz = gzipSync(body, { level: 9 }).length;
  total += gz;
  rows.push([f, body.length, gz]);
}
for (const [f, raw, gz] of rows) {
  console.log(`${(gz / 1024).toFixed(1).padStart(8)} KB gzip  ${(raw / 1024).toFixed(1).padStart(8)} KB raw  ${f}`);
}
const totalKb = total / 1024;
console.log(`Initial JS: ${totalKb.toFixed(1)} KB gzip (budget ${BUDGET_KB} KB).`);
if (rows.some(([f]) => /charts-/.test(f))) {
  console.error("The chart library is in the initial download - it must stay a lazily loaded chunk.");
  process.exit(1);
}
if (totalKb > BUDGET_KB) {
  console.error(`Over budget by ${(totalKb - BUDGET_KB).toFixed(1)} KB. Lazy-load the new code (React.lazy / dynamic import).`);
  process.exit(1);
}
