import { readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join, relative } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * UI-H Wave 0 guard (owner decision 3, 24 Oct: ONE navigation style for every tab / sub-tab row). A hand-made tab row
 * outside src/ui/SubTabs.tsx fails this test. It looks for the marks of a tab row in app source (tests and stories
 * excluded):
 * - `role="tablist"` / `role="tab"` (a hand-built tab row), or the Radix tabs package;
 * - an underline tab row: a link or button with `aria-current` and a `border-b-2` underline in the same file.
 * Two components are allowed (owner W0-2): <PageTabs> (primary, page-level, underline) and <SubTabs> (secondary,
 * inside the content, filled) - both drawn by src/ui/SubTabs.tsx; timeframes use <TimeframePicker> (TF-1), built on SubTabs.
 * Chips stay for filters only.
 *
 * LEGACY lists the rows not yet moved, each with its reason; the page waves empty it. A file that no longer needs its
 * entry fails too, so the list only shrinks.
 */
const LEGACY: Record<string, string> = {
  "src/components/primitives/Tabs.tsx": "the old Radix Tabs primitive (screener library tools); moves with Wave 3 (Find setups)",
  "src/copilot/components/CopilotTabBar.tsx": "Copilot's own tab bar with icons and badges; moves with Wave 4 (Copilot)",
  "src/site/SitePage.tsx": "the public About pages (outside the app); moves with the public-site pass",
};

/** The only files that may draw a tab row. */
const ALLOWED = ["src/ui/SubTabs.tsx", "src/ui/PageTabs.tsx"];

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
const SRC = join(ROOT, "src");

function files(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) files(full, out);
    else if (/\.tsx?$/.test(name) && !/\.(test|stories|fixture)\.tsx?$/.test(name)) out.push(full);
  }
  return out;
}

/** The reasons a source text looks like a hand-made tab row (empty when it does not). */
function tabRowMarks(text: string): string[] {
  const code = text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/[^\n]*/g, "$1");
  const marks: string[] = [];
  if (/role=["{]\s*["']?tablist|role=["']tab["']|role:\s*["']tab["']/.test(code)) marks.push("tab roles");
  if (/@radix-ui\/react-tabs/.test(code)) marks.push("Radix tabs");
  if (/aria-current=/.test(code) && /\bborder-b-2\b/.test(code)) marks.push("underline tab row");
  return marks;
}

describe("UI-H H2 guard: every tab / sub-tab row is a SubTabs", () => {
  const found = new Map<string, string[]>();
  for (const f of files(SRC)) {
    const rel = relative(ROOT, f).split("\\").join("/");
    if (ALLOWED.includes(rel)) continue;
    const marks = tabRowMarks(readFileSync(f, "utf8"));
    if (marks.length) found.set(rel, marks);
  }

  it("no hand-made tab row outside PageTabs / SubTabs (src/ui/PageTabs.tsx, src/ui/SubTabs.tsx)", () => {
    const fresh = [...found].filter(([f]) => !(f in LEGACY)).map(([f, m]) => `${f}: ${m.join(", ")}`);
    expect(fresh).toEqual([]);
  });

  it("the legacy list only shrinks: every entry still has a row to move", () => {
    expect(Object.keys(LEGACY).filter((f) => !found.has(f))).toEqual([]);
  });

  it("the marks it looks for", () => {
    expect(tabRowMarks('<div role="tablist">')).toEqual(["tab roles"]);
    expect(tabRowMarks('<button role="tab">')).toEqual(["tab roles"]);
    expect(tabRowMarks('<Link aria-current="page" className="border-b-2">')).toEqual(["underline tab row"]);
    expect(tabRowMarks('<SubTabs label="X" items={[]} value="a" />')).toEqual([]);
    expect(tabRowMarks('// role="tablist" in a comment')).toEqual([]);
  });
});
