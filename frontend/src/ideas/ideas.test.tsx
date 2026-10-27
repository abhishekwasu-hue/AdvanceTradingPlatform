import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import fixture from "../../e2e/fixtures/ideas_v2.json";
import { allNavItems, isItemVisible, navItemFor } from "../shell/navModel";
import { closedFlag } from "../shell/pageFlags";
import { shellKeyFromPath, shellTitle } from "../shell/shellRoutes";
import screenerSource from "../pages/ScreenerPage.tsx?raw";
import type { IdeaLibrary, IdeaSetting } from "./api";
import { ADOPT_NOTE, IdeaLibraryView, adoptPathFor } from "./IdeaLibraryV2";
import pageSource from "./IdeaLibraryV2.tsx?raw";
import {
  ALL_GROUPS, IDEAS_ADDRESS, IDEA_FLAG, MAX_SUGGESTIONS, defaultText, ideaScanFromSearch, ideasIn, rangeText, scanName,
  screenerPathFor, suggestIdeas,
} from "./model";

/** Idea library slice 1: the page's helpers, the cards from the server's own payload (e2e/fixtures/ideas_v2.json is
 * checked against the backend by tests/test_idea_library.py), the education guard on what the page shows, and the
 * shell wiring behind `ui_v2` + `idea_library`. */

const LIB = fixture as unknown as IdeaLibrary;
const ORDER = LIB.ideas.map((i) => i.id);

function render(props: { initialGroup?: string; initialAnswers?: Record<string, string>; adoptOn?: boolean } = {}, library: IdeaLibrary = LIB): string {
  return renderToStaticMarkup(<MemoryRouter><IdeaLibraryView library={library} {...props} /></MemoryRouter>);
}

/** The ideas the server maps onto a template (slice 3: T2 -> A2, T5 -> A5; the fixture is the payload with strategy
 * templates on). */
const ADOPTABLE = LIB.ideas.filter((i) => i.adopt.available).map((i) => i.id);

/** The text a trader reads (markup stripped), without the scan formulas (ScreenQL code, shown folded for the curious). */
function readable(html: string): string {
  return html.replace(/<code[^>]*>[\s\S]*?<\/code>/g, " ").replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"")
    .replace(/&amp;/g, "&").replace(/\s+/g, " ");
}

describe("the helpers", () => {
  it("groups: all, or one group in the library's order", () => {
    expect(ideasIn(LIB.ideas, ALL_GROUPS).map((i) => i.id)).toEqual(["T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"]);
    expect(ideasIn(LIB.ideas, "trend").map((i) => i.id)).toEqual(["T1", "T5", "T6"]);
    expect(ideasIn(LIB.ideas, "options").map((i) => i.id)).toEqual(["T8"]);
  });

  it("the beginner path suggests nothing until every question is answered, then two or three ideas", () => {
    const qs = LIB.beginner.questions;
    expect(suggestIdeas(qs, {}, ORDER)).toEqual([]);
    expect(suggestIdeas(qs, { style: "trend", time: "few" }, ORDER)).toEqual([]);
    const picks = suggestIdeas(qs, { style: "trend", time: "few", options: "no" }, ORDER);
    expect(picks).toEqual(["T1", "T5", "T2"]);
    expect(picks.length).toBeLessThanOrEqual(MAX_SUGGESTIONS);
    expect(suggestIdeas(qs, { style: "range", time: "often", options: "yes" }, ORDER)).toEqual(["T2", "T3", "T8"]);   // a seller sees the pack
    expect(suggestIdeas(qs, { style: "range", time: "few", options: "yes" }, ORDER)).toEqual(["T2", "T1", "T8"]);
  });

  it("Try as a scan: the Screener address and its reading round-trip; anything else is ignored", () => {
    expect(screenerPathFor("T5", "above")).toBe("/screener?idea=T5&scan=above");
    expect(ideaScanFromSearch("?idea=T5&scan=above")).toEqual({ idea: "T5", scan: "above" });
    expect(ideaScanFromSearch("?idea=T9&scan=above")).toBeNull();
    expect(ideaScanFromSearch("?idea=T1&scan=../x")).toBeNull();
    expect(ideaScanFromSearch("")).toBeNull();
    expect(scanName({ name: "Range edges" }, { label: "Upper edge" })).toBe("Range edges - Upper edge");
  });

  it("a setting's starting value in words", () => {
    const choice = LIB.ideas[0].settings.find((s) => s.key === "trend_degree") as IdeaSetting;
    const num = LIB.ideas[0].settings.find((s) => s.key === "area_tolerance_pct") as IdeaSetting;
    expect(defaultText(choice)).toBe("Small");
    expect(rangeText(choice)).toBe("");
    expect(defaultText(num)).toBe("0.5");
    expect(rangeText(num)).toBe("0.1 to 5");
  });
});

describe("the cards", () => {
  const html = render();

  it("shows the education frame first and every idea as a card", () => {
    expect(html.indexOf("Education, not advice")).toBeGreaterThan(-1);
    expect(html.indexOf("Education, not advice")).toBeLessThan(html.indexOf("data-idea=\"T1\""));
    for (const id of ORDER) expect(html).toContain(`data-idea="${id}"`);
    expect(html).toContain("No results are claimed");
  });

  it("an open scan is a link to the Screener; a closed one says so; what a scan cannot read yet comes later", () => {
    expect(html).toContain('href="/screener?idea=T5&amp;scan=above"');
    expect(html).toContain('href="/screener?idea=T2&amp;scan=upper"');
    expect(html).not.toContain("/screener?idea=T1&amp;");                       // market zones are off in the fixture
    const t1 = html.slice(html.indexOf('data-idea="T1"'), html.indexOf('data-idea="T2"'));
    expect(t1).toContain("not switched on for your account yet");
    // slice 4: T6 reads the channel line (breakout functions only: open here); what it cannot read yet still comes later
    const t6 = html.slice(html.indexOf('data-idea="T6"'), html.indexOf('data-idea="T7"'));
    expect(t6).toContain('href="/screener?idea=T6&amp;scan=rising"');
    expect(t6).toContain('href="/screener?idea=T6&amp;scan=falling"');
    expect(t6).toContain("come later");
    const t7 = html.slice(html.indexOf('data-idea="T7"'), html.indexOf('data-idea="T8"'));   // slice 3: T7 has scans (zones off here)
    // slice 4: head and shoulders - the neckline retests need no zones (open here); the double top / bottom and the right
    // shoulders read zones (off here), so the card says the other scans are not switched on yet
    expect(t7).toContain('data-state="scan-some-off"');
    expect(t7).toContain('href="/screener?idea=T7&amp;scan=hs_top_retest"');
    expect(t7).toContain('href="/screener?idea=T7&amp;scan=hs_bottom_retest"');
    expect(t7).not.toContain('href="/screener?idea=T7&amp;scan=top"');
    expect(t7).toContain("a sloped neckline comes later");
  });

  it("is a white box: the full logic, the settings with their starting values, the decisions and the scan formula", () => {
    const t5 = html.slice(html.indexOf('data-idea="T5"'), html.indexOf('data-idea="T6"'));
    for (const part of ["In plain words", "How it reads the chart", "How a signal reads", "Settings you choose", "Always your decision",
      "Where it comes from", "The scan in full"]) expect(t5).toContain(part);
    expect(t5).toContain("starts at 10 (1 to 100)");
    expect(t5).toContain("RetestEntry(&quot;up&quot;, $retest_of, $retest_max_bars, TRUE)");
  });

  it("without strategy templates on, every card says adopting comes later, and nothing offers LIVE", () => {
    expect(ADOPTABLE).toEqual(["T2", "T5"]);
    expect(html).not.toContain("data-adopt=");
    expect((html.match(/data-state="adopt-later"/g) ?? []).length).toBe(LIB.ideas.length);
    expect(readable(html)).not.toMatch(/\blive\b/i);
  });

  it("slice 3: T2 and T5 offer Adopt as a paper strategy into the Paper bot form with their template and settings", () => {
    const on = render({ adoptOn: true });
    const t2 = on.slice(on.indexOf('data-idea="T2"'), on.indexOf('data-idea="T3"'));
    expect(t2).toContain('data-adopt="A2"');
    expect(t2).toContain('href="/deployments?template=A2&amp;timeframe=15m&amp;set.swing_degree=1"');
    const t5 = on.slice(on.indexOf('data-idea="T5"'), on.indexOf('data-idea="T6"'));
    expect(t5).toContain('data-adopt="A5"');
    expect(t5).toContain('href="/deployments?template=A5&amp;timeframe=15m&amp;set.retest_of=swing&amp;set.retest_max_bars=10'
      + '&amp;set.vol_mult=1.5&amp;set.range_mode=fixed"');
    for (const card of [t2, t5]) {
      expect(card).toContain('data-state="adopt-ready"');
      expect(card).toContain('data-state="adopt-note"');
      expect(readable(card)).toContain("runs on paper only");
      expect(readable(card)).toContain("is not checked yet");                    // where the paper strategy reads less
    }
    expect((on.match(/data-adopt=/g) ?? []).length).toBe(2);                       // the other ideas still come later
    expect((on.match(/data-state="adopt-later"/g) ?? []).length).toBe(LIB.ideas.length - 2);
    expect(adoptPathFor({ adopt: { available: false } })).toBeNull();
    expect(ADOPT_NOTE).not.toMatch(/\b(buy|sell|target|stop|profit|live)\b/i);
  });

  it("the beginner suggestions link to the cards", () => {
    const answered = render({ initialAnswers: { style: "range", time: "few", options: "yes" } });
    expect(answered).toContain('data-block="idea-suggestions"');
    expect(answered).toContain('href="#idea-T2"');
    expect(answered).toContain('href="#idea-T8"');
    expect(render()).not.toContain('data-block="idea-suggestions"');
  });

  it("a group shows only its ideas", () => {
    const range = render({ initialGroup: "range" });
    expect(range).toContain('data-idea="T2"');
    expect(range).not.toContain('data-idea="T1"');
  });
});

describe("the education guard on what the page shows", () => {
  const text = readable(render({ initialAnswers: { style: "trend", time: "few", options: "yes" } }));
  // the page's own words and the content together: CLAUDE.md "Signal language" for public / education surfaces
  it.each([
    /\b(buy|buying|bought|sell|selling|sold|target|targets|stop|stoploss|sl)\b/i,
    /stop-loss|take profit|win rate|success rate|guarantee|profit/i,
    /\b(nifty|banknifty|sensex|reliance|infosys|hdfc|tcs)\b/i,
    /₹|\bRs\.?\s|\bINR\b/,
  ])("nothing matches %s", (re) => {
    expect(text).not.toMatch(re);
  });

  it("no number except a setting's own value, a timeframe word, an idea id or a count", () => {
    const rest = text
      .replace(/starts at [\d.]+( \([\d.]+ to [\d.]+\))?/g, "")         // the user's settings, labelled as his
      .replace(/\b\d{1,2}-(minute|hour)\b/g, "")
      .replace(/\bT[1-8]\b/g, "")
      .replace(/(All ideas|Trend|Range|Reversal at areas|Patterns|Options sellers) \d\b/g, "$1");   // the group counts
    expect(rest).not.toMatch(/\d/);
  });

  it("the page's own source writes no advice either", () => {
    expect(pageSource).not.toMatch(/\b(buy|sell|target|stop-loss)\b/i);
  });
});

describe("the shell: behind ui_v2 + idea_library", () => {
  it("/ideas is the Ideas page: Strategies › Ideas (UX3-9)", () => {
    expect(shellKeyFromPath(IDEAS_ADDRESS)).toBe("ideas");
    expect(shellKeyFromPath("/ideas/")).toBe("ideas");
    expect(shellTitle("ideas")).toBe("Strategies");
    expect(navItemFor("ideas")?.section).toBe("strategies");
  });

  it("flag off: no navigation entry and the address shows Coming soon", () => {
    const item = allNavItems().find((i) => i.key === "ideas");
    expect(item?.flag).toBe(IDEA_FLAG);
    expect(isItemVisible(item!, { on: () => false, role: "OWNER" })).toBe(false);
    expect(isItemVisible(item!, { on: (f) => f === IDEA_FLAG, role: "OWNER" })).toBe(true);
    expect(closedFlag("ideas", { answered: true, on: () => false })).toBe(IDEA_FLAG);
    expect(closedFlag("ideas", { answered: true, on: (f) => f === IDEA_FLAG })).toBeNull();
  });

  it("the Screener opens an idea's scan through its own validated builder path", () => {
    expect(screenerSource).toContain("ideaScanFromSearch(location.search)");
    expect(screenerSource).toContain("screenerApi.stages({ source: scan.text, base_tf: scan.base_tf, params: scan.params })");
  });
});
