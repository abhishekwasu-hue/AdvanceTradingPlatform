import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { INTRADAY_SOURCE, isIntradayLibrary, paramValueText, scannersById, unsetText, type IntradayLibrary } from "./intraday";
import intradaySample from "./intradaySample.json";
import { ScreenGallery } from "./ScreenGallery";
import { inPack, NO_FILTER, packsShown, type GalleryFilter, type Taxonomy } from "./taxonomy";
import offSample from "./taxonomySample.json";
import onSample from "./taxonomyIntradaySample.json";

// M94 PR-2: the "Intraday" chip and the intraday scanners' cards, on the server's own samples (backend
// tests/test_m94_pr2_scanners.py checks both are what the routes serve: the pack off, and the pack on).
const off = offSample as unknown as Taxonomy;
const on = onSample as unknown as Taxonomy;
const lib = intradaySample as unknown as IntradayLibrary;
const FOOTER = "Information and education only, not investment advice.";
const render = (tax: Taxonomy, initial: Partial<GalleryFilter> = {}, withSettings = true) =>
  renderToStaticMarkup(<ScreenGallery taxonomy={tax} canOpen={() => true} onOpen={() => undefined} initial={initial}
                                      intraday={withSettings ? scannersById(lib) : {}} />);
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
const cards = (html: string) => [...html.matchAll(/data-screen-id="([^"]+)"/g)].map((m) => m[1]);

describe("Intraday pack chip (M94 PR-2)", () => {
  it("is not drawn while the pack is off: the gallery is as before", () => {
    expect(off.screens.some((s) => s.source === INTRADAY_SOURCE)).toBe(false);
    expect(packsShown(off)).toEqual([]);
    const html = render(off);
    expect(html).not.toContain('aria-label="Pack"');
    expect(html).not.toContain("Intraday Gappers");
    expect(html).not.toContain(FOOTER);
    expect(inPack(off, null)).toBe(off);
  });

  it("offers Intraday with its count when the pack is on, and every screen without it", () => {
    expect(packsShown(on)).toEqual([{ id: "intraday", label: "Intraday", count: 13 }]);
    const html = render(on);
    expect(html).toContain('role="group" aria-label="Pack"');
    expect(text(html)).toContain(`All screens ${on.total}`);
    expect(text(html)).toContain("Intraday 13");
    expect(cards(html)).toHaveLength(on.screens.length);
  });

  it("filters to the thirteen scanners (M99 P2: Graded Key Levels); the rail, chips, count and matrix follow", () => {
    const html = render(on, { pack: "intraday" });
    expect(cards(html)).toEqual(lib.scanners.map((s) => s.id));
    expect(cards(html).every((id) => id.startsWith("intraday."))).toBe(true);
    expect(text(html)).toContain("13 screens · All methods → All setups");
    const picked = inPack(on, "intraday");
    expect(picked.total).toBe(13);
    expect(Object.values(picked.matrix).flatMap((r) => Object.values(r)).reduce((a, b) => a + b, 0)).toBe(13);
    expect(html).toMatch(/aria-pressed="true"[^>]*>Intraday/);
    // a setup chip within the pack: the reversal scanners only
    const rev = render(on, { pack: "intraday", setup: "reversal" });
    expect(cards(rev)).toEqual(["intraday.top_reversal", "intraday.bottom_reversal", "intraday.rsi_extreme"]);
  });

  it("puts the footer on each intraday card and on no other", () => {
    const html = render(on);
    expect(html.match(/data-testid="screen-source-note"/g)).toHaveLength(13);
    expect(html.split(FOOTER).length - 1).toBe(13);
    const breakouts = render(on, { ...NO_FILTER, query: "Opening range" });
    expect(cards(breakouts).length).toBeGreaterThan(0);
    expect(breakouts).not.toContain(FOOTER);
  });

  it("shows each scanner's settings: reference values, ATP defaults and Not set", () => {
    const html = render(on, { pack: "intraday" });
    expect(html.match(/data-testid="scanner-settings"/g)).toHaveLength(13);
    const t = text(html);
    expect(t).toContain("Settings (4) · Not set: 3 of 4. The run asks for them.");                 // Gappers
    expect(t).toContain("Reference value - edit before use");
    expect(t).toContain("Settings (1)");                                                          // VWAP Break: all set
    expect(html).toMatch(/data-param="pre_vol"[\s\S]*?Not set/);
    expect(render(on, { pack: "intraday" }, false)).not.toContain('data-testid="scanner-settings"');
  });

  it("formats values and the Not set line", () => {
    expect(isIntradayLibrary(lib)).toBe(true);
    expect(isIntradayLibrary({})).toBe(false);
    const s1 = lib.scanners[0];
    expect(s1.footer).toBe(FOOTER);
    expect(s1.params.map(paramValueText)).toEqual(["2", "Not set", "Not set", "Not set"]);
    expect(paramValueText({ name: "vwap", value: true, doc: "", ref: false, note: "ATP default" })).toBe("Yes");
    expect(unsetText(s1)).toBe("Not set: 3 of 4. The run asks for them.");
    expect(unsetText(lib.scanners.find((s) => s.id === "intraday.new_day_high")!)).toBe("Not set: 1 of 8. The run asks for it.");
    expect(unsetText(lib.scanners.find((s) => s.id === "intraday.vwap_break")!)).toBe("");
  });

  it("uses no advice wording, no Devanagari and English only", () => {
    const words = text(render(on, { pack: "intraday" })).toLowerCase();
    for (const w of [" buy ", " sell ", "recommend", " target", " stop", " best ", "guarantee"]) expect(words).not.toContain(w);
    expect(words).not.toMatch(/[\u0900-\u097F]/);
  });
});
