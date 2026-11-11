import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { isIntradayLibrary, paramValueText, scannersById, unsetText } from "./intraday";
import { ScreenGallery } from "./ScreenGallery";
import { SD_CHIP, SD_SOURCE, type SdLibrary } from "./supplyDemand";
import sdSample from "./sdSample.json";
import { inPack, packsShown, type GalleryFilter, type Taxonomy } from "./taxonomy";
import offSample from "./taxonomySample.json";
import onSample from "./taxonomySdSample.json";

// M97 PR-2: the "Supply & Demand" chip and the scanners' cards, on the server's own samples (backend
// tests/test_m97_pr2_scanners.py checks both are what the routes serve: the pack off, and the pack on).
const off = offSample as unknown as Taxonomy;
const on = onSample as unknown as Taxonomy;
const lib = sdSample as unknown as SdLibrary;
const FOOTER = "Information and education only, not investment advice.";
const IDS = ["supply_demand.demand_enter", "supply_demand.supply_enter", "supply_demand.zone_formed", "supply_demand.zone_violated",
  "supply_demand.near_fresh_zone"];
const render = (tax: Taxonomy, initial: Partial<GalleryFilter> = {}) =>
  renderToStaticMarkup(<ScreenGallery taxonomy={tax} canOpen={() => true} onOpen={() => undefined} initial={initial}
                                      intraday={scannersById(lib)} />);
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const cards = (html: string) => [...html.matchAll(/data-screen-id="([^"]+)"/g)].map((m) => m[1]);

describe("Supply & Demand pack chip (M97 PR-2)", () => {
  it("is not drawn while the pack is off", () => {
    expect(off.screens.some((s) => s.source === SD_SOURCE)).toBe(false);
    expect(packsShown(off).some((p) => p.id === SD_SOURCE)).toBe(false);
    expect(text(render(off))).not.toContain(SD_CHIP);
  });

  it("offers Supply & Demand with its count when the pack is on", () => {
    expect(packsShown(on)).toEqual([{ id: SD_SOURCE, label: SD_CHIP, count: 5 }]);
    const t = text(render(on));
    expect(t).toContain("Supply & Demand 5");
    expect(t).toContain(`All screens ${on.total}`);
  });

  it("filters to the five scanners, each with its settings and the footer", () => {
    const html = render(on, { pack: SD_SOURCE });
    expect(cards(html)).toEqual(IDS);
    expect(inPack(on, SD_SOURCE).total).toBe(5);
    expect(html.match(/data-testid="scanner-settings"/g)).toHaveLength(5);
    expect(html.split(FOOTER).length - 1).toBe(5);
    const t = text(html);
    expect(t).toContain("Reference value - edit before use");
    expect(t).toContain("Settings (11) · Not set: 1 of 11. The run asks for it.");      // near a fresh zone (M98-P1 Q2: +1)
    expect(html).toMatch(/data-param="within_pct"[\s\S]*?Not set/);
  });

  it("formats the values and uses no advice wording", () => {
    expect(isIntradayLibrary(lib)).toBe(true);
    const near = lib.scanners.find((s) => s.id === "supply_demand.near_fresh_zone")!;
    // M98-P1 Q1: the leg-in reference 1.0 x shows as "1"; the leg-out stays 1.5; Q2: skip_flat_bars on shows as "Yes"
    expect(near.params.map(paramValueText).slice(0, 5)).toEqual(["Not set", "20", "1", "0.7", "1.5"]);
    expect(paramValueText(near.params[near.params.length - 1])).toBe("Yes");
    expect(unsetText(lib.scanners[0])).toBe("");
    const words = text(render(on, { pack: SD_SOURCE })).toLowerCase();
    for (const w of [" buy ", " sell ", "recommend", " target", " stop", " best ", "guarantee"]) expect(words).not.toContain(w);
    expect(words).not.toMatch(/[\u0900-\u097F]/);
  });
});
