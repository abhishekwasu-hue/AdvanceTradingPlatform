import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * UI-H colour accents guards (owner order CA2 and the owner's scope note, 25 Oct): "CA1-CA4 are project-wide and
 * permanent - they live ONLY in the shared colour_v2 tokens and the UI-H shared components ... NO page-local colours".
 *
 * 1. CA2: the blue -> violet gradient never reaches text, tables, the option chain, forms or charts - no gradient mark
 *    (the gradient classes, GRADIENT_FILL / HERO_CARD / AOI_HIGHLIGHT, the NewBadge, a `gradient` Button) in a chain,
 *    table, form, ticket or chart component.
 * 2. Scope: outside src/ui, the token files and the Button, no file names an accent token itself (the gradient, the
 *    secondary violet, a section tint, a tone colour) or draws its own CSS gradient. Pages adopt the accents only
 *    through the shared components and src/ui/accents.ts / src/ui/tone.ts helpers.
 *
 * LEGACY lists the gradients that predate the order (none of them the accent gradient), each with its reason; a file
 * that no longer needs its entry fails too, so the list only shrinks.
 */
const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const SRC = join(ROOT, "src");

function files(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) files(full, out);
    else if (/\.(tsx?|css)$/.test(name) && !/\.(test|stories|fixture)\.tsx?$/.test(name)) out.push(full);
  }
  return out;
}

const rel = (full: string) => relative(ROOT, full).split("\\").join("/");
const strip = (text: string) => text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:"'`])\/\/.*$/gm, "$1");
const ALL = files(SRC).map((f) => ({ path: rel(f), text: strip(readFileSync(f, "utf8")) }));

// --- 1. CA2: no gradient in chains, tables, forms, tickets or charts -------------------------------------------------

/** Chain, table, form, ticket and chart components: by folder, or by name. */
const NO_GRADIENT_DIRS = ["src/optionChain/", "src/chart/", "src/charting/", "src/oi/", "src/fno/", "src/optionsBuilder/", "src/terminal/"];
const NO_GRADIENT_NAME = /(?:Chain|Table|Form|Ticket|Chart)\w*\.tsx?$/;

const GRADIENT_MARKS: [string, RegExp][] = [
  ["a gradient class", /accent-gradient|aoi-highlight/],
  ["a gradient helper", /\b(?:GRADIENT_FILL|HERO_CARD|AOI_HIGHLIGHT)\b/],
  ["the New badge", /<NewBadge\b/],
  ["a gradient Button", /<Button\b[^>]*\sgradient\b|\bgradient:\s*true\b/],
];

export function gradientFindings(path: string, text: string): string[] {
  if (path === "src/ui/accents.ts" || path === "src/ui/NewBadge.tsx" || path === "src/components/primitives/Button.tsx") return [];
  const covered = NO_GRADIENT_DIRS.some((d) => path.startsWith(d)) || NO_GRADIENT_NAME.test(path);
  if (!covered) return [];
  return GRADIENT_MARKS.filter(([, re]) => re.test(text)).map(([what]) => `${path}: ${what}`);
}

// --- 2. Scope: no page-local accent colours ---------------------------------------------------------------------------

/** Where the accents may be named: the shared components, the token files and the one Button. */
const SHARED = (path: string) => path.startsWith("src/ui/") || path.startsWith("src/styles/") || path === "src/components/primitives/Button.tsx";

/** Gradients drawn before the order, none of them the accent gradient (Wave 4 moves the Copilot ones). */
const LEGACY: Record<string, string> = {
  "src/copilot/tabs/lab/StudyView.tsx": "the Study view's loss -> profit scale bar (a meaning scale, not decoration); Wave 4 (Copilot)",
  "src/copilot/tabs/IdeaBuilderTab.tsx": "the Idea builder's AI progress bar (AI violet / cyan); Wave 4 (Copilot)",
  "src/copilot/copilot.css": "the Copilot's own AI surfaces (AI violet / cyan); Wave 4 (Copilot)",
};

const ACCENT_TOKEN = /(?<![\w-])(?:[a-z]+:)*!?(?:bg|text|border|ring|outline|from|via|to|fill|stroke|divide|shadow|decoration)-(?:accent-2|soft-accent-2|sec-[a-z]+(?:-soft)?|tone-(?:profit|loss|risk|info))(?:\/[\d.]+)?(?![\w-])/;
const ACCENT_CLASS = /\b(?:bg-accent-gradient|accent-gradient-border|aoi-highlight)\b/;
const ACCENT_VAR = /--(?:accent-gradient|accent-2|soft-accent-2|grad-(?:from|to)|sec-[a-z]+|tone-(?:profit|loss|risk|info))\b/;
const OWN_GRADIENT = /\b(?:linear|radial|conic)-gradient\(|(?<![\w-])bg-gradient-to-[a-z]+/;

export function scopeFindings(path: string, text: string): string[] {
  if (SHARED(path)) return [];
  const out: string[] = [];
  if (ACCENT_TOKEN.test(text)) out.push(`${path}: an accent token class (a section tint, a tone colour or the violet) outside src/ui`);
  if (ACCENT_CLASS.test(text)) out.push(`${path}: the accent gradient class outside src/ui`);
  if (ACCENT_VAR.test(text)) out.push(`${path}: an accent CSS variable outside src/ui`);
  if (OWN_GRADIENT.test(text) && !(path in LEGACY)) out.push(`${path}: its own CSS gradient`);
  return out;
}

describe("UI-H colour accents guards", () => {
  it("CA2: no gradient in a chain, table, form, ticket or chart component", () => {
    expect(ALL.flatMap((f) => gradientFindings(f.path, f.text))).toEqual([]);
  });

  it("scope: no page or feature file defines a gradient, a section tint or a tone colour itself", () => {
    expect(ALL.flatMap((f) => scopeFindings(f.path, f.text))).toEqual([]);
  });

  it("the legacy list only shrinks: each entry still draws its gradient", () => {
    for (const path of Object.keys(LEGACY)) {
      const file = ALL.find((f) => f.path === path);
      expect(file, path).toBeDefined();
      expect(OWN_GRADIENT.test(file!.text), `${path} no longer needs its LEGACY entry`).toBe(true);
    }
  });

  it("the guards catch what they are meant to catch", () => {
    expect(gradientFindings("src/optionChain/ChainRowV2.tsx", '<tr className="bg-accent-gradient" />')).toHaveLength(1);
    expect(gradientFindings("src/terminal/PanelsV2.tsx", "<Button variant=\"primary\" gradient>Place</Button>")).toHaveLength(1);
    expect(gradientFindings("src/settings/AlertsForm.tsx", "import { GRADIENT_FILL } from '../ui/accents';")).toHaveLength(1);
    expect(gradientFindings("src/positions/PositionsTable.tsx", "<NewBadge />")).toHaveLength(1);
    expect(gradientFindings("src/dashboard/MarketStripV2.tsx", "HERO_CARD")).toEqual([]);
    expect(scopeFindings("src/pages/Foo.tsx", '<span className="md:bg-sec-markets-soft text-sec-markets" />')).toHaveLength(1);
    expect(scopeFindings("src/pages/Foo.tsx", '<div className="bg-tone-profit" />')).toHaveLength(1);
    expect(scopeFindings("src/pages/Foo.tsx", '<div style={{ background: "linear-gradient(90deg, red, blue)" }} />')).toHaveLength(1);
    expect(scopeFindings("src/pages/Foo.tsx", '<div className="bg-gradient-to-r from-brand to-ai" />')).toHaveLength(1);
    expect(scopeFindings("src/pages/Foo.tsx", 'color: "rgb(var(--sec-admin))"')).toHaveLength(1);
    expect(scopeFindings("src/pages/Foo.tsx", '<div className="bg-up-tint text-up" />')).toEqual([]);
    expect(scopeFindings("src/ui/accents.ts", "bg-sec-markets-soft")).toEqual([]);
  });
});
