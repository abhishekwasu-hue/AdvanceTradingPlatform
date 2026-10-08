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

// Copilot redesign: the Copilot route's own JS (the page chunk, everything it imports statically and the default tab,
// Market Pulse, with its imports - not the shared shell counted above) and the 3D "AI Core" chunk (three.js, loaded
// when the browser is idle) have their own budgets.
const COPILOT_BUDGET_KB = Number(process.env.COPILOT_JS_BUDGET_KB ?? 120);
const CORE3D_BUDGET_KB = Number(process.env.COPILOT_3D_BUDGET_KB ?? 180);
let manifest;
try {
  manifest = JSON.parse(readFileSync(join(dist, ".vite", "manifest.json"), "utf8"));
} catch {
  console.error("dist/.vite/manifest.json not found - build with `build.manifest: true` (vite.config.ts).");
  process.exit(2);
}
const gz = (file) => gzipSync(readFileSync(join(dist, file)), { level: 9 }).length;
const shellFiles = new Set([...files].map((f) => f.replace(/^\//, "")));
/** A manifest entry's own file plus every chunk it imports statically, minus `skip`. */
function closure(key, skip, seen = new Set()) {
  const entry = manifest[key];
  if (!entry || seen.has(key)) return seen;
  seen.add(key);
  for (const dep of entry.imports ?? []) closure(dep, skip, seen);
  return seen;
}
function measure(keys, skip) {
  const out = new Map();
  for (const key of keys) {
    for (const k of closure(key, skip)) {
      const file = manifest[k].file;
      if (!skip.has(file) && file.endsWith(".js")) out.set(file, gz(file));
    }
  }
  return out;
}
// A source module is keyed by its path - unless Rollup hoisted it into a shared chunk, which is keyed "_<name>-<hash>.js".
const keyOf = (suffix, name) => Object.keys(manifest).find((k) => k.endsWith(suffix))
  ?? Object.keys(manifest).find((k) => manifest[k].name === name && manifest[k].file.endsWith(".js") && manifest[k].isDynamicEntry);
const pageKey = keyOf("src/pages/AiCopilotPage.tsx", "AiCopilotPage");
const pulseKey = keyOf("src/copilot/tabs/MarketPulseTab.tsx", "MarketPulseTab");
const coreKey = keyOf("src/copilot/components/AICore3D.tsx", "AICore3D");
if (!pageKey || !pulseKey || !coreKey) {
  console.error("The Copilot page, its Market Pulse tab or the AI Core 3D chunk is missing from the manifest.");
  process.exit(1);
}
const route = measure([pageKey, pulseKey], shellFiles);
const routeKb = [...route.values()].reduce((a, b) => a + b, 0) / 1024;
const core = measure([coreKey], new Set([...shellFiles, ...route.keys()]));
const coreKb = [...core.values()].reduce((a, b) => a + b, 0) / 1024;
for (const [f, size] of route) console.log(`${(size / 1024).toFixed(1).padStart(8)} KB gzip  copilot route  ${f}`);
for (const [f, size] of core) console.log(`${(size / 1024).toFixed(1).padStart(8)} KB gzip  copilot 3D     ${f}`);
console.log(`Copilot route JS: ${routeKb.toFixed(1)} KB gzip (budget ${COPILOT_BUDGET_KB} KB; plus the shared shell ${totalKb.toFixed(1)} KB = ${(routeKb + totalKb).toFixed(1)} KB on a first visit).`);
console.log(`Copilot 3D chunk: ${coreKb.toFixed(1)} KB gzip (budget ${CORE3D_BUDGET_KB} KB, loaded when the browser is idle).`);
if ([...route.keys()].some((f) => core.has(f)) || [...route.keys()].some((f) => /AICore3D/.test(f))) {
  console.error("The 3D scene is in the Copilot's first download - it must stay a lazily loaded chunk.");
  process.exit(1);
}
if (routeKb > COPILOT_BUDGET_KB) {
  console.error(`The Copilot route is over budget by ${(routeKb - COPILOT_BUDGET_KB).toFixed(1)} KB.`);
  process.exit(1);
}
if (coreKb > CORE3D_BUDGET_KB) {
  console.error(`The 3D chunk is over budget by ${(coreKb - CORE3D_BUDGET_KB).toFixed(1)} KB.`);
  process.exit(1);
}
