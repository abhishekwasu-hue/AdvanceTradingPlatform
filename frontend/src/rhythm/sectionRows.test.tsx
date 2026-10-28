import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { hasCallWords } from "../chartAnalyze/signalLanguage";
import { RhythmBriefV2, RhythmSectionV2 } from "./DailyRhythmCardV2";
import { CLOSING, PRE_MARKET } from "./fixtures";
import { cleanSection } from "./model";
import { dayLabel, hasTypedView } from "./SectionRowsV2";
import type { RhythmSection } from "./types";

/** UX-11b C2: the Today briefs numbers first - typed rows rendered by kind, colour by meaning with a mark, text in Details. */

const html = (node: React.ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
const text = (out: string) => out.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/&quot;/g, "\"").replace(/&#x27;/g, "'").replace(/\s+/g, " ");
const section = (brief: typeof PRE_MARKET, id: string) => brief.sections.find((s) => s.id === id)!;
const typed = (s: RhythmSection) => html(<RhythmSectionV2 section={s} typed />);

describe("UX-11b the Morning brief", () => {
  it("renders every typed section by its kind, not as sentences", () => {
    const out = html(<RhythmBriefV2 brief={PRE_MARKET} phase="pre_market" kind="pre_market" onKind={() => undefined} />);
    for (const s of PRE_MARKET.sections) expect(out).toContain(`data-rows="${s.kind}"`);
    expect(text(out)).not.toContain("Global cues: positive (S&P 500 futures");
    expect(hasCallWords(text(out))).toBe(false);
  });

  it("global cues: the mood chip and a market table with Signed changes; a stale market says delayed", () => {
    const out = typed(section(PRE_MARKET, "mood"));
    expect(out).toContain('data-chip="mood-positive"');
    expect(out).toMatch(/data-signed="up"[^>]*>.*\+0\.62%/);
    expect(out).toMatch(/data-signed="down"[^>]*>.*0\.35%/);
    expect(out).toContain('data-chip="delayed"');
    expect(out).toContain('data-chip="vix-normal"');
    expect(text(out)).toContain("31% of closes below");
    expect(text(out)).toMatch(/FII net \(₹ cr\) · Wed 21 Oct ▼ −1,250/);
  });

  it("structure: one row per index with trend, event and location chips; the areas only in Details", () => {
    const out = typed(section(PRE_MARKET, "structure"));
    expect(out).toContain('data-chip="trend-up"');
    expect(out).toContain('data-chip="trend-down"');
    expect(out).toContain('data-chip="event-bos-up"');
    expect(out).toContain('data-chip="event-choch"');
    expect(out).toMatch(/data-chip="event-choch" class="[^"]*text-warn/);
    expect(out).toContain('data-chip="location-in_seller"');
    expect(out).toContain('data-chip="stale"');
    const details = out.slice(out.indexOf("data-details="));
    expect(text(details)).toContain("NIFTY 50: buyer area 24,850–24,900 · seller area 25,300–25,350");
    expect(text(out.slice(0, out.indexOf("data-details=")))).not.toMatch(/24,850|25,300/);
  });

  it("setups and positions: a table with the side chip, Signed P&L and the amber near-invalidation chip", () => {
    const setups = typed(section(PRE_MARKET, "setups"));
    expect(setups).toContain('data-direction="long"');
    expect(text(setups.slice(setups.indexOf("data-details=")))).toContain("pullback reached a buyer area");
    const pos = typed(section(PRE_MARKET, "positions"));
    expect(pos).toMatch(/data-signed="up"[^>]*>.*\+₹1,240/);
    expect(pos).toMatch(/data-chip="near" class="[^"]*text-warn/);
  });

  it("the risk budget: a bar coloured by use and the band chip", () => {
    const out = typed(section(PRE_MARKET, "budget"));
    expect(out).toContain('data-meaning-bar="inside"');
    expect(out).toContain('data-chip="budget-inside"');
    const near = typed({ ...section(PRE_MARKET, "budget"), rows: [{ type: "budget", left: 900, limit: 5000, used: 4100, losses_in_row: 2, pause_after: 3 }] });
    expect(near).toContain('data-meaning-bar="near"');
    expect(near).toMatch(/data-chip="losses-in-row" class="[^"]*text-warn/);
    expect(text(near)).toContain("Losses in a row 2 / 3");
    const reached = typed({ ...section(PRE_MARKET, "budget"), rows: [{ type: "budget", left: 0, limit: 5000, used: 5000, losses_in_row: 0, pause_after: 3 }] });
    expect(reached).toContain('data-meaning-bar="reached"');
  });
});

describe("UX-11b the Wrap", () => {
  it("index closes with Signed changes, the breadth bar, the sectors, the day block and the next session chips", () => {
    const market = typed(section(CLOSING, "market"));
    expect(text(market)).toContain("25,012.40");
    expect(market).toContain("data-breadth");
    expect(text(market)).toContain("▲ 1,412 rose");
    expect(text(market)).toMatch(/Weakest: Metal ▼ −1\.10%/);
    const day = typed(section(CLOSING, "day"));
    expect(day).toContain('data-chip="limits-inside"');
    expect(day).toContain('data-progress="Discipline score (your own plan)"');
    const next = typed(section(CLOSING, "next"));
    expect(next).toContain('data-chip="next-session"');
    expect(text(next)).toContain("Fri 23 Oct · RBI policy");
    expect(next).toMatch(/data-chip="event" class="[^"]*text-warn/);              // new entries pause in its window
  });

  it("the disclaimer once as the footnote; the brief's note moves into Details", () => {
    const out = html(<RhythmBriefV2 brief={CLOSING} phase="closing" kind="closing" onKind={() => undefined} />);
    expect((out.match(/data-page-footnote/g) ?? []).length).toBe(1);
    expect(out).toContain('data-details="rhythm.note"');
  });
});

describe("UX-11b fallbacks and guards", () => {
  it("a section without rows or with an unknown kind shows its lines", () => {
    expect(hasTypedView({ ...section(CLOSING, "day"), rows: [] })).toBe(false);
    expect(hasTypedView({ ...section(CLOSING, "day"), kind: "lines" })).toBe(false);
    expect(text(typed({ ...section(CLOSING, "day"), rows: [] }))).toContain("Booked P&L today: +₹860.");
  });

  it("a row whose words read as a call is left out", () => {
    const bad: RhythmSection = { ...section(PRE_MARKET, "setups"), rows: [
      { type: "setup", symbol: "ABC", side: "LONG", source: "strategy", detail: "buy above the high" },
      { type: "setup", symbol: "XYZ", side: null, source: "scan", detail: "matched your scan." },
    ] };
    expect(cleanSection(bad).rows!.map((r) => (r as { symbol: string }).symbol)).toEqual(["XYZ"]);
  });

  it("calendar days read as the day itself", () => {
    expect(dayLabel("2026-10-23")).toBe("Fri 23 Oct");
    expect(dayLabel(null)).toBe("");
  });
});
