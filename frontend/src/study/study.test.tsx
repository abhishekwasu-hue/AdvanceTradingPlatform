import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { FORMATS, SECTIONS, initialSheet, saveBody, sheetProblem, studyFormats, timeframeWords, toggle } from "./model";
import { MyStudiesV2 } from "./MyStudiesV2";
import workspaceSource from "../copilot/workspace/ChartWorkspaceV2.tsx?raw";
import sheetSource from "./SaveStudySheetV2.tsx?raw";
import listSource from "./MyStudiesV2.tsx?raw";
import JournalPageV2 from "../journal/JournalPageV2";

/**
 * M77 SX-7 (owner order M77 PD-1 / PD-4): the Save study sheet and My studies - pure parts, the request it sends
 * (references and his own words only, never a number from the page), the in-place wiring (no navigation away from the
 * tab), and the Journal's My studies tab.
 */

describe("SX-7 model", () => {
  it("opens with his open chart's timeframe, every section on and PDF - nothing else chosen for him", () => {
    const s = initialSheet("15m");
    expect(s.timeframes).toEqual(["15m"]);
    expect(s.sections).toEqual(SECTIONS.map((x) => x.id));
    expect(s.format).toBe("pdf");
    expect(initialSheet("1m").timeframes).toEqual([]);                      // a timeframe the study does not offer: none ticked
    expect(FORMATS.map((f) => f.id)).toEqual(["pdf", "png", "png-one", "csv", "json"]);
  });
  it("toggles keep the list's own order", () => {
    expect(toggle(["15m"], "1d", ["1d", "1h", "15m", "5m"])).toEqual(["1d", "15m"]);
    expect(toggle(["1d", "15m"], "1d", ["1d", "1h", "15m", "5m"])).toEqual(["15m"]);
  });
  it("says plainly why Save cannot run", () => {
    const s = initialSheet("15m");
    expect(sheetProblem(s, "pdf", "15m")).toBeNull();
    expect(sheetProblem({ ...s, timeframes: [] }, "pdf", "15m")).toBe("Tick at least one timeframe.");
    expect(sheetProblem({ ...s, sections: [] }, "pdf", "15m")).toBe("Tick at least one section.");
    expect(sheetProblem({ ...s, sections: ["notes"] }, "png", "15m")).toBe("An image needs the Charts section ticked.");
    expect(sheetProblem({ ...s, timeframes: ["1d"] }, "png-one", "15m")).toContain("chart you have open");
    expect(sheetProblem({ ...s, notes: "x".repeat(5001) }, "pdf", "15m")).toContain("longer than 5,000");
    expect(sheetProblem({ ...s, sections: ["cover", "trade_plan"] }, "csv", "15m")).toBe("CSV needs a section with a table.");
  });
  it("sends references and his own words only - no price, no area, no result", () => {
    const body = saveBody("NSE:NIFTY 50", { ...initialSheet("15m"), title: "  My look ", notes: "Watch the area" });
    expect(Object.keys(body).sort()).toEqual(["instrument", "notes", "sections", "timeframes", "title"]);
    expect(body.title).toBe("My look");
    expect(JSON.stringify({ ...body, notes: "", title: "" }).replace("NIFTY 50", "")).not.toMatch(/\d{3,}/);
    expect(saveBody("NSE:X", initialSheet("15m"))).not.toHaveProperty("notes");
  });
  it("offers the files a saved study can give", () => {
    expect(studyFormats({ sections: ["charts"], timeframes: ["1d", "15m"] }).map((f) => f.label))
      .toEqual(["PDF", "Image", "Image Daily", "Image 15 min", "CSV", "JSON"]);
    expect(studyFormats({ sections: ["notes"], timeframes: ["15m"] }).map((f) => f.format)).toEqual(["pdf", "csv", "json"]);
    expect(timeframeWords(["1h", "5m"])).toBe("1 hour, 5 min");
  });
});

describe("SX-7 api", () => {
  afterEach(() => vi.unstubAllGlobals());
  it("downloads from the study's own file route and reads the server's file name", async () => {
    const seen: string[] = [];
    vi.stubGlobal("fetch", async (url: string) => {
      seen.push(url);
      return new Response("x", { status: 200, headers: { "content-disposition": 'attachment; filename="study-nifty-50-20261102-1005-15m.png"' } });
    });
    const { studyApi } = await import("./api");
    const file = await studyApi.download(7, "png-one", { tf: "15m", cvd: true });
    expect(file.filename).toBe("study-nifty-50-20261102-1005-15m.png");
    await studyApi.download(7, "csv");
    await studyApi.download(7, "json", { cvd: true });
    expect(seen.map((u) => u.replace(/^.*\/api\/v1/, ""))).toEqual(["/studies/7/png?tf=15m&cvd=true", "/studies/7/csv", "/studies/7/json"]);
  });
});

describe("SX-7 in place", () => {
  it("never navigates: no link, no navigate, no window.open in the sheet, the list or the workspace wiring", () => {
    for (const src of [sheetSource, listSource, workspaceSource]) {
      expect(src).not.toMatch(/navigate\(|<Link\b|window\.open|location\.(assign|replace|href)/);
    }
    expect(workspaceSource).toContain('features.on("study_export")');
    expect(workspaceSource).toContain("<SaveStudySheetV2");
    expect(workspaceSource).toContain('<Sheet open={studiesOpen}');
  });
  it("My studies starts by loading his list", () => {
    vi.stubGlobal("fetch", () => new Promise(() => {}));
    const html = renderToStaticMarkup(<MyStudiesV2 />);
    expect(html).toContain('data-my-studies="loading"');
    expect(html).toContain("Loading your studies");
    expect(html).toContain("data-studies-search");
    vi.unstubAllGlobals();
  });
  it("the Journal shows a My studies tab only with the flag", () => {
    const api = new Proxy({}, { get: () => () => new Promise(() => {}) }) as never;
    expect(renderToStaticMarkup(<JournalPageV2 api={api} studiesOn />)).toContain("My studies");
    expect(renderToStaticMarkup(<JournalPageV2 api={api} />)).not.toContain("My studies");
  });
});
