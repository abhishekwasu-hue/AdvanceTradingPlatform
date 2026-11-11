/**
 * M97 PR-5 (Z6 + Z7): the trading-style presets of the Supply & Demand zones overlay (they only preselect timeframes;
 * an edit is kept as that style's own), the options saved in the server chart prefs (cleaned like every pref), and
 * the overlay toggle that tells the server it is on (the Copilot's chart answer reads it).
 */
import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import {
  DEFAULT_SD_OVERLAY, SD_STYLE_TIMEFRAMES, SD_STYLES, cleanSdOverlay, editTimeframes, pickStyle, styleTimeframes, type SdOverlayPrefs,
} from "./sdStyles";
import { SdZonesBar, styleOptionLabel } from "./SdZonesBar";
import { applyPatch, cleanPrefs, DEFAULT_CHART_PREFS } from "./useChartPrefs";
import { shownSdTimeframes } from "./sdZones";

describe("style presets", () => {
  it("are the owner's ladder, in order", () => {
    expect(SD_STYLES).toEqual(["investor", "positional", "swing", "short_term", "intraday", "scalper"]);
    expect(SD_STYLE_TIMEFRAMES).toEqual({ investor: ["1M"], positional: ["1w"], swing: ["1d", "2h"], short_term: ["1h", "30m"],
      intraday: ["15m", "10m"], scalper: ["5m", "3m"] });
  });

  it("a style only preselects its extra chips; the chart's own is always drawn, then the style's higher ones (M98 T1)", () => {
    const swing = pickStyle(DEFAULT_SD_OVERLAY, "swing");
    expect(swing).toEqual({ ...DEFAULT_SD_OVERLAY, style: "swing", timeframes: ["1d", "2h"] });
    expect(shownSdTimeframes(swing.timeframes, "15min")).toEqual(["15m", "2h", "1d"]);
    expect(shownSdTimeframes(swing.timeframes, "day")).toEqual(["1d"]);                                                 // none higher than D
    expect(shownSdTimeframes(pickStyle(DEFAULT_SD_OVERLAY, "intraday").timeframes, "15min")).toEqual(["15m"]);          // 10m is lower: not drawn
    expect(shownSdTimeframes(pickStyle(DEFAULT_SD_OVERLAY, "intraday").timeframes, "5min")).toEqual(["5m", "10m", "15m"]);
    expect(pickStyle(swing, null)).toEqual({ ...swing, style: null });                                                  // own: as they are
  });

  it("an edit is kept as that style's own and comes back when the style is chosen again; back to the ladder clears it", () => {
    const edited = editTimeframes(pickStyle(DEFAULT_SD_OVERLAY, "swing"), ["1h", "1d"]);
    expect(edited.presets).toEqual({ swing: ["1h", "1d"] });
    expect(styleTimeframes(edited, "swing")).toEqual(["1h", "1d"]);
    const away = pickStyle(edited, "investor");
    expect(away.timeframes).toEqual(["1M"]);
    expect(pickStyle(away, "swing").timeframes).toEqual(["1h", "1d"]);
    expect(editTimeframes(pickStyle(edited, "swing"), ["2h", "1d"]).presets).toEqual({});
    const own = editTimeframes(DEFAULT_SD_OVERLAY, ["1w", "1d"]);
    expect(own).toEqual({ ...DEFAULT_SD_OVERLAY, timeframes: ["1d", "1w"] });                                        // no style: no preset edit
    expect(styleOptionLabel(edited, "swing")).toBe("Swing · 1H, D");
    expect(styleOptionLabel(edited, "scalper")).toBe("Scalper · 5m, 3m");
  });
});

describe("saved in the server chart prefs", () => {
  it("a stored value is cleaned: unknown styles and timeframes dropped, at most four, Show violated only when true", () => {
    expect(cleanSdOverlay(null)).toEqual(DEFAULT_SD_OVERLAY);
    expect(cleanSdOverlay({ style: "daytrader", timeframes: ["7m", "1d", "1d", "5m", "1h", "2h", "1w"], presets: { swing: ["1d"], nope: ["1d"], scalper: [] },
                            show_violated: "yes" }))
      .toEqual({ style: null, timeframes: ["5m", "1h", "2h", "1d"], presets: { swing: ["1d"] }, show_violated: false });
    const prefs = cleanPrefs({ sd_zones: true, sd_overlay: { style: "intraday", timeframes: ["15m", "10m"], show_violated: true } });
    expect(prefs.sd_zones).toBe(true);
    expect(prefs.sd_overlay).toEqual({ style: "intraday", timeframes: ["10m", "15m"], presets: {}, show_violated: true });
    const next = applyPatch(DEFAULT_CHART_PREFS, { sd_overlay: pickStyle(DEFAULT_SD_OVERLAY, "positional") as SdOverlayPrefs });
    expect(next.sd_overlay.timeframes).toEqual(["1w"]);
    expect(applyPatch(next, { sd_zones: true }).sd_overlay.timeframes).toEqual(["1w"]);      // one patch never loses the other
  });

  it("the options bar names the chosen style and shows Show violated as saved", () => {
    const html = renderToStaticMarkup(<SdZonesBar chartTf="5min" shown={["5m", "10m", "15m"]} options={{ ...pickStyle(DEFAULT_SD_OVERLAY, "intraday"), show_violated: true }}
      onChange={() => {}} note={null} />);
    expect(html).toContain('data-sd-style="intraday"');
    expect(html).toContain("Intraday");
    expect(html).toContain('data-sd-own="5m"');
    expect(html).toMatch(/aria-pressed="true" data-sd-tf="10m"/);
    expect(html).toMatch(/data-sd-violated[^>]*checked/);
  });

  it("turning the layer on or off in the Indicators menu saves sd_zones (the Copilot reads it on the server)", () => {
    const page = readFileSync("src/chart/ChartsPageV2.tsx", "utf8");
    expect(page).toMatch(/id === SD_LAYER_ID && "layers" in change\) prefs\.update\(\{ sd_zones: change\.layers\.includes\(SD_LAYER_ID\) \}\)/);
    expect(page).toContain("prefs.prefs.sd_overlay");
  });
});
