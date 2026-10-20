#!/usr/bin/env node
// UI2-0 (UI v2 proposal §8, §12): v2 screens use the semantic tokens only (surface-*, fg, fg-muted, border, border-subtle,
// brand, up, down, warn, warn-tint, on-brand). A raw palette hue (`text-sky-300`, `bg-violet-500/10`, `border-amber-400`),
// white / black, or an arbitrary colour (`bg-[#0ea5e9]`, `text-[rgb(...)]`) in a v2 file fails this check, so colour
// keeps carrying meaning only and both themes and the colour-blind option keep working.
//
// Files checked: every `*V2*.tsx` under src/, and every .tsx under src/ui/ (the v2 building blocks).
//   node scripts/check-v2-colours.mjs            # exit 0 = clean, 1 = findings
//   node scripts/check-v2-colours.mjs FILE...    # check the named files instead
import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const HUES = ["slate", "gray", "zinc", "neutral", "stone", "red", "orange", "amber", "yellow", "lime", "green", "emerald",
  "teal", "cyan", "sky", "blue", "indigo", "violet", "purple", "fuchsia", "pink", "rose"];
const UTILITIES = ["bg", "text", "border", "border-[trblxyse]", "ring", "ring-offset", "outline", "divide", "from", "via",
  "to", "fill", "stroke", "shadow", "accent", "caret", "decoration", "placeholder"];
const UTIL = `(?:${UTILITIES.join("|")})`;
// A Tailwind colour class at a word start, with any variant prefix (`hover:`, `dark:`, `md:`) and optional `!`.
const RAW_HUE = new RegExp(`(?<![\\w-])!?${UTIL}-(?:${HUES.join("|")})-(?:50|[1-9]00|950)(?:\\/[\\d.]+|\\/\\[[^\\]]+\\])?(?![\\w-])`, "g");
const WHITE_BLACK = new RegExp(`(?<![\\w-])!?${UTIL}-(?:white|black)(?:\\/[\\d.]+)?(?![\\w-])`, "g");
const ARBITRARY = new RegExp(`(?<![\\w-])!?${UTIL}-\\[(?:#|rgb|rgba|hsl|hsla|color)[^\\]]*\\]`, "g");

/** Every raw colour in `text`, as { line, match }. Exported for the unit test (test/check-v2-colours.test.ts). */
export function findRawColours(text) {
  const out = [];
  const lines = text.split("\n");
  lines.forEach((line, i) => {
    for (const re of [RAW_HUE, WHITE_BLACK, ARBITRARY]) {
      re.lastIndex = 0;
      for (const m of line.matchAll(re)) out.push({ line: i + 1, match: m[0] });
    }
  });
  return out;
}

/** True for a file this check covers: `*V2*.tsx` anywhere under src/, or any .tsx under src/ui/. */
export function isV2File(path) {
  const p = path.split(sep).join("/");
  if (!p.endsWith(".tsx")) return false;
  const name = p.slice(p.lastIndexOf("/") + 1);
  return /V2/.test(name) || /(^|\/)src\/ui\//.test(p);
}

function walk(dir, files = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, files);
    else files.push(full);
  }
  return files;
}

function main() {
  const root = join(dirname(fileURLToPath(import.meta.url)), "..");
  const named = process.argv.slice(2);
  const files = named.length ? named : walk(join(root, "src")).filter(isV2File);
  let findings = 0;
  for (const f of files) {
    for (const { line, match } of findRawColours(readFileSync(f, "utf8"))) {
      findings += 1;
      console.error(`${relative(root, f)}:${line}: raw colour "${match}" - use a semantic token (fg, fg-muted, brand, up, down, warn, surface-*, border-subtle)`);
    }
  }
  console.log(`v2 colour check: ${files.length} file(s), ${findings} finding(s).`);
  process.exit(findings ? 1 : 0);
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) main();
