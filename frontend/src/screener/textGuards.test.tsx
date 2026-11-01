import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { DISCLAIMER_TEXT } from "../ui/Disclaimer";
import { EvidenceView } from "./EvidencePanel";
import evidenceSample from "./evidenceSample.json";
import type { EvidencePack } from "./evidence";
import ScanAlertPanel, { DONE } from "./ScanAlertPanel";

/**
 * Owner order SCR-FIX, PR E1: one "not advice" line per page (and per dialog, which covers the page on a phone), in its
 * footer; every other explanation sits in ⓘ Details (src/ui/Details), moved, never deleted.
 */
const read = (path: string) => readFileSync(path, "utf8");
const count = (html: string, needle: string) => html.split(needle).length - 1;
/** The html with every Details block cut out: what a phone shows before anything is opened. */
const outsideDetails = (html: string) => html.replace(/<details data-details[\s\S]*?<\/details>/g, "");

describe("SCR-FIX E1: one not-advice line per page", () => {
  it("the Find setups page has one footnote; the universe note and the run's disclaimer are in Details (v2)", () => {
    const page = read("src/pages/ScreenerPage.tsx");
    expect(count(page, "<PageFootnote")).toBe(1);
    expect(page).toContain("<div className=\"mt-space-6\"><PageFootnote /></div>");          // M49 U6: in the v2 layout
    expect(page).toContain('<Details id="screener.universe.about"');
    expect(page).toContain('<Details id="screener.results.about" label="About these results"><p>{run.disclaimer}</p></Details>');
  });

  it("the evidence dialog: one footnote at its foot, the server's note in Details", () => {
    const pack = evidenceSample as unknown as EvidencePack;
    const html = renderToStaticMarkup(<MemoryRouter><EvidenceView pack={pack} hiddenBlocks={new Set()} showFailed={false} onSend={() => undefined} /></MemoryRouter>);
    expect(count(html, "data-disclaimer")).toBe(1);
    expect(html).toContain(DISCLAIMER_TEXT);
    expect(html).toContain('data-details="screener.evidence.about"');
    expect(html).toContain("Not a recommendation.");                              // the server's note: moved, not deleted
    expect(outsideDetails(html)).not.toContain("Not a recommendation.");
    expect(html.lastIndexOf("data-page-footnote")).toBeGreaterThan(html.lastIndexOf("Send this chart to my Telegram"));
  });

  it("Tell me when it matches: one footnote; how he is told is in Details; the done line is state only", () => {
    const api = { rules: async () => [], create: async () => { throw new Error("not here"); }, setStatus: async () => { throw new Error("not here"); } };
    const html = renderToStaticMarkup(
      <MemoryRouter><ScanAlertPanel name="My scan" symbols={["A", "B"]} scanTf="1d" screenId={9} save={async () => 9} api={api} initialRules={[]} /></MemoryRouter>);
    expect(count(html, "data-disclaimer")).toBe(1);
    expect(html).toContain('data-details="screener.alert.how"');
    expect(html).toContain('href="/settings/alerts"');
    expect(DONE).not.toMatch(/advice/);
  });

  it("the idea and library dialogs keep their server notes in Details and add one footnote each", () => {
    for (const file of ["src/screener/IdeaPanel.tsx", "src/screener/LibraryTools.tsx"]) {
      const src = read(file);
      expect(count(src, "<PageFootnote")).toBe(1);
      expect(src).not.toMatch(/<p className="[^"]*">\{result\.disclaimer\}<\/p>/);
    }
  });
});
