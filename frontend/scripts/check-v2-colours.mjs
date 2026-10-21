#!/usr/bin/env node
// UI2-0 (UI v2 proposal §8, §12): v2 screens use the semantic tokens only (surface-*, fg, fg-muted, border, border-subtle,
// brand, up, down, warn, warn-tint, on-brand). Findings:
//  - a raw palette hue class (`text-sky-300`, `bg-violet-500/10`, `border-amber-400`), white / black, or an arbitrary
//    colour class (`bg-[#0ea5e9]`, `text-[rgb(...)]`);
//  - a literal colour in an inline style or an SVG attribute (`style={{ color: "#fff" }}`, `fill="#0ea5e9"`,
//    `stroke: "rgb(1, 2, 3)"`) - `rgb(var(--token))` is the allowed form.
// Comments are ignored, so a comment may name a class it forbids.
//
// Files checked (explicit, so an unrelated "V2" name is not swept in):
//  - every .ts / .tsx under src/ui/ (the v2 building blocks), except tests, stories and fixtures;
//  - every `*V2.tsx` anywhere under src/ (UI2-1 onward: DashboardV2.tsx, shell/SidebarV2.tsx, ...);
//  - every .ts / .tsx under src/chart/ (UI2-4b1: the v2 chart, its theme and settings), except tests;
//  - every .ts / .tsx under src/broker/ (P2b: the Broker page, its prompt, panel and nav dot - both shells), except tests;
//  - minus EXCLUDED: files named "V2" for another reason.
//   node scripts/check-v2-colours.mjs            # exit 0 = clean, 1 = findings
//   node scripts/check-v2-colours.mjs FILE...    # check the named files instead
import { readFileSync, readdirSync, realpathSync, statSync } from "node:fs";
import { dirname, join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

/** Not UI v2: the AMW-1 plan limits card ("plan_limits_v2"). */
export const EXCLUDED = ["src/components/PlanLimitsV2.tsx"];

const HUES = ["slate", "gray", "zinc", "neutral", "stone", "red", "orange", "amber", "yellow", "lime", "green", "emerald",
  "teal", "cyan", "sky", "blue", "indigo", "violet", "purple", "fuchsia", "pink", "rose"];
const UTILITIES = ["bg", "text", "border", "border-[trblxyse]", "ring", "ring-offset", "outline", "divide", "from", "via",
  "to", "fill", "stroke", "shadow", "accent", "caret", "decoration", "placeholder"];
const UTIL = `(?:${UTILITIES.join("|")})`;
// A Tailwind colour class at a word start, with any variant prefix (`hover:`, `dark:`, `md:`) and optional `!`.
const RAW_HUE = new RegExp(`(?<![\\w-])!?${UTIL}-(?:${HUES.join("|")})-(?:50|[1-9]00|950)(?:\\/[\\d.]+|\\/\\[[^\\]]+\\])?(?![\\w-])`, "g");
const WHITE_BLACK = new RegExp(`(?<![\\w-])!?${UTIL}-(?:white|black)(?:\\/[\\d.]+)?(?![\\w-])`, "g");
const ARBITRARY = new RegExp(`(?<![\\w-])!?${UTIL}-\\[(?:#|rgb|rgba|hsl|hsla|color)[^\\]]*\\]`, "g");
// A literal colour value: #rgb / #rrggbb / #rrggbbaa, or rgb() / rgba() / hsl() / hsla() not built on a token var().
const LITERAL = String.raw`(?:#[0-9a-fA-F]{3,8}\b|(?:rgba?|hsla?)\((?!\s*var\()[^)]*\))`;
// ... as a style property (`color: "#fff"`, `backgroundColor: 'rgb(...)'`) or an SVG / JSX attribute (`fill="#fff"`).
const STYLE_PROPS = "color|background|backgroundColor|borderColor|borderTopColor|borderBottomColor|borderLeftColor|"
  + "borderRightColor|outlineColor|fill|stroke|stopColor|floodColor|lightingColor|caretColor|textDecorationColor|"
  + "boxShadow|textShadow|accentColor|columnRuleColor";
const STYLE_VALUE = new RegExp(`\\b(?:${STYLE_PROPS})\\s*:\\s*["'\`][^"'\`]*?${LITERAL}`, "g");
const ATTR_VALUE = new RegExp(`(?<![\\w-])(?:fill|stroke|stop-color|stopColor|flood-color|floodColor|color)\\s*=\\s*\\{?\\s*["'\`]\\s*${LITERAL}`, "g");

/** The source with comments blanked out (line numbers kept). A `//` right after ":" (a URL) is not a comment. */
export function stripComments(text) {
  const blank = (m) => m.replace(/[^\n]/g, " ");
  return text
    .replace(/\/\*[\s\S]*?\*\//g, blank)
    .replace(/(^|[^:"'`\\])\/\/[^\n]*/g, (m, lead) => lead + blank(m.slice(lead.length)));
}

/** Every raw colour in `text`, as { line, match }. Exported for the unit test (test/check-v2-colours.test.ts). */
export function findRawColours(text) {
  const out = [];
  stripComments(text).split("\n").forEach((line, i) => {
    for (const re of [RAW_HUE, WHITE_BLACK, ARBITRARY, STYLE_VALUE, ATTR_VALUE]) {
      re.lastIndex = 0;
      for (const m of line.matchAll(re)) out.push({ line: i + 1, match: m[0] });
    }
  });
  return out;
}

/** True for a file this check covers (see the header). `path` may be absolute or relative to the frontend root. */
export function isV2File(path) {
  const p = path.split(sep).join("/");
  const at = p.lastIndexOf("src/");
  if (at === -1) return false;
  const rel = p.slice(at);
  if (EXCLUDED.includes(rel)) return false;
  if (/\.(test|stories|fixture)\.tsx?$/.test(rel)) return false;
  if (/^src\/(ui|chart|broker)\/.+\.tsx?$/.test(rel)) return true;
  return /^src\/(.+\/)?[^/]+V2\.tsx$/.test(rel);
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
  const files = named.length ? named : walk(join(root, "src")).filter((f) => isV2File(relative(root, f)));
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

/** True when this module is the program node was started with - compared as real paths, so a symlinked checkout, a
 * relative argv[1] or a different spelling of the same file still runs the check (and an import never does). */
export function isMain(argv1 = process.argv[1], self = fileURLToPath(import.meta.url)) {
  if (!argv1) return false;
  try {
    return realpathSync(argv1) === realpathSync(self);
  } catch {
    return false;
  }
}

if (isMain()) main();
