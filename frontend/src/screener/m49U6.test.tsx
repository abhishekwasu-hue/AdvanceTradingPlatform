import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import ScreenerPage from "../pages/ScreenerPage";
import { V2_ALIASES, shellKeyFromPath, v2RedirectFor } from "../shell/shellRoutes";
import { PAGE_COMPONENTS_V2 } from "../shell/pagesV2";
import { FeaturesProvider } from "../ui/useFeatures";
import contract from "./builderTexts.json";
import CustomScanV2 from "./CustomScanV2";
import { FunnelCanvas, STARTERS } from "./FunnelCanvas";
import { newStage, type Registry } from "./model";
import { StartScan, startWays } from "./StartScan";
import type { TaxScreen } from "./taxonomy";

/**
 * M49 U6 (owner-approved mockup docs/ui/U6_SCREENER.html): Find setups with ONE "Start a scan" chooser, Custom scan
 * folded in as "Conditions" (its address opens the same page with Conditions open), "Matched N of M" said once after a
 * run, and no "Autopilot" wording. The Playwright spec e2e/m49-find-setups-v2.e2e.ts checks the same on a real page.
 */
const reg = contract.registry as unknown as Registry;
const V2 = { ui_v2: true, screener_v2: true, screener_copilot: true };
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
const count = (s: string, part: string) => s.split(part).length - 1;
const page = (features: Record<string, boolean>, node: React.ReactNode, at = "/screener") => renderToStaticMarkup(
  <MemoryRouter initialEntries={[at]}><FeaturesProvider userKey="t" value={features}>{node}</FeaturesProvider></MemoryRouter>,
);
const source = readFileSync("src/pages/ScreenerPage.tsx", "utf8");
const v2Block = source.slice(source.indexOf("  if (universeV2) {"), source.indexOf("  return (\n    <PlainTimeframes.Provider value={universeV2}>"));
const SCREEN = (id: string, name: string): TaxScreen => ({ id, source: "starter", name, definition: `${name} in words`, method: "m", setup: "s",
  tags: { timeframe_family: [], direction: "neutral", data_tier: [], difficulty: "easy", universe_default: "" }, evidence_ready: false, fitness_available: false });

describe("M49 U6: one Start a scan chooser", () => {
  it("the page has exactly one start block, with one chooser of three ways", () => {
    const html = page(V2, <ScreenerPage />);
    expect(count(html, 'data-testid="start-scan"')).toBe(1);
    expect(count(html, 'role="tablist"')).toBe(1);
    expect(text(html)).toContain("Start a scan");
    // the three old starts are gone as separate blocks: the ready-scans card, the canvas's templates, "From an idea"
    expect(text(html)).not.toContain("Start with a ready scan");
    expect(text(html)).not.toContain("Start with a template or add your first condition.");
    expect(source).not.toContain('"idea-open-dialog"');
    expect(startWays({ ready: 3, words: true })).toEqual(["ready", "template", "words"]);
    expect(startWays({ ready: 0, words: false })).toEqual(["template"]);
  });

  it("Ready scan runs, Template fills the Conditions, Blank starts from zero", () => {
    const props = { onWay: () => undefined, ways: ["ready", "template", "words"] as const, ready: [SCREEN("starter.momentum", "Momentum")],
      libraryCount: 24, onRunReady: () => undefined, onSeeAll: () => undefined, templates: STARTERS, onTemplate: () => undefined,
      onBlank: () => undefined, listName: "NIFTY 50" };
    const ready = text(renderToStaticMarkup(<StartScan {...props} way="ready" />));
    expect(ready).toContain("One tap runs it on NIFTY 50.");
    expect(ready).toContain("See all ready scans (24)");
    const template = text(renderToStaticMarkup(<StartScan {...props} way="template" />));
    for (const t of STARTERS) expect(template).toContain(t.name);
    expect(template).toContain("Blank: I add conditions");
    expect(template).not.toContain("Run");
  });
});

describe("M49 U6: Custom scan folded in as Conditions", () => {
  it("Conditions is there, one line until opened", () => {
    const html = page(V2, <ScreenerPage />);
    expect(html).toContain('data-testid="conditions"');
    expect(text(html)).toContain("Conditions ( 0 )");
    expect(text(html)).toContain("Pick a scan above, or add your first condition.");
    expect(html).not.toContain('id="conditions-body"');
    expect(html).toMatch(/data-testid="conditions-toggle"[^>]*>Show/);
  });

  it("/screener/custom keeps its address and opens the same page with Conditions open", () => {
    expect(V2_ALIASES.find((a) => a.path === "/screener/custom")?.page).toBe("scanner");
    expect(shellKeyFromPath("/screener/custom")).toBe("scanner");
    expect(v2RedirectFor("/scanner", "", { signedIn: true, on: () => true })).toBe("/screener/custom");
    expect(PAGE_COMPONENTS_V2.scanner).toBeDefined();
    const html = page(V2, <CustomScanV2 />, "/screener/custom");
    expect(count(html, 'data-testid="start-scan"')).toBe(1);
    expect(html).toContain('id="conditions-body"');
    expect(html).toMatch(/data-testid="conditions-toggle"[^>]*>Hide/);
    // the custom scanner itself stays inside Conditions (CLAUDE.md §8), opened on request
    expect(html).toContain('data-testid="custom-scanner-toggle"');
    expect(text(html)).toContain("More filters: indicator, structure, option chain");
  });

  it("with the screener off, the address still shows the custom scanner on its own", () => {
    const wrapper = readFileSync("src/screener/CustomScanV2.tsx", "utf8");
    expect(wrapper).toContain('features.on("screener_v2") ? <ScreenerPage {...props} conditionsOpen /> : <ScannerPage {...props} />');
  });
});

describe("M49 U6: Matched once, no Autopilot", () => {
  it("before a run nothing says Matched; the phone bar keeps Save and Run only", () => {
    const html = page(V2, <ScreenerPage />);
    expect(text(html)).not.toMatch(/Matched/);
    expect(html).toContain('data-testid="screener-bottom-bar"');
    // a disabled Run says why: before any condition, "Pick a scan first"
    expect(source).toContain('const runWhy = blocker && universeV2 && !hasStages ? "Pick a scan first" : blocker;');
  });

  it("the v2 layout says Matched in one place - the results header, after a run", () => {
    const code = v2Block.split("\n").filter((l) => !/^\s*(\/\/|\{\/\*)/.test(l)).join("\n");     // its comments aside
    expect(code.match(/\bMatched\b/g)).toHaveLength(1);
    expect(v2Block).toContain("{ranCounts && (");
    expect(v2Block).toContain('data-testid="results-matched"');
    expect(v2Block).not.toContain("<MatchedFoot");
    // the canvas inside the block has no count of its own
    const canvas = renderToStaticMarkup(<FunnelCanvas stages={[newStage(reg)]} registry={reg} scanTf="1d" universe={50} funnel={null} fresh={false}
      matched={12} problems={{}} onChange={() => undefined} inBlock />);
    expect(text(canvas)).not.toContain("Matched");
    expect(canvas).not.toContain('aria-label="Conditions"');
  });

  it("no Autopilot anywhere on the page; the next step after a run is Tell me when it matches", () => {
    const html = page(V2, <ScreenerPage />);
    expect(text(html)).not.toMatch(/autopilot/i);
    expect(source).not.toMatch(/autopilot/i);
    expect(v2Block).toContain("{run && ideaOn && (");
    expect(v2Block).toContain("Tell me when it matches");
  });

  it("with ui_v2 off the page is as before (no chooser, its own header and tools)", () => {
    const html = page({ screener_v2: true }, <ScreenerPage />);
    expect(html).not.toContain('data-testid="start-scan"');
    expect(html).not.toContain('data-testid="conditions"');
    expect(html).toContain('aria-label="Universe"');
  });
});
