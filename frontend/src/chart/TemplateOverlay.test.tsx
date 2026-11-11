import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import chartsSource from "./ChartsPageV2.tsx?raw";
import type { TemplateOverlay } from "../strategyTemplates/api";
import { M94_FOOTER } from "../strategyTemplates/api";
import { TemplateLegend, partMark, partSign, templateLayers, type TemplateOverlayState } from "./TemplateOverlay";

/** M94 F16: the template overlay on a chart - marks per part and its legend, only on demand. Made-up bars only. */
const T = (m: number) => new Date(Date.UTC(2026, 1, 2, 3, 45 + 5 * m)).toISOString();
const CANDLES = Array.from({ length: 6 }, (_, i) => ({ timestamp: T(i) }));
const REPLY: TemplateOverlay = {
  template: { id: "H8", name: "Made-up reversal" }, symbol: "MADEUP", exchange: "NSE", base_tf: "5m", footer: M94_FOOTER, available: true,
  parts: [
    { part: "setup", side: "long", label: "Setup (upward)", kept: false, matched: false, reason: null, rows: [], hits: [T(1), T(2)] },
    { part: "trigger", side: "long", label: "Trigger (upward)", kept: false, matched: true, reason: null, rows: [], hits: [T(3)] },
    { part: "invalidation", side: "long", label: "Wrong beyond (upward)", kept: true, matched: null, reason: null, rows: [], hits: [] },
    { part: "invalidation", side: "short", label: "Wrong beyond (downward)", kept: false, matched: false, reason: null, rows: [], hits: [T(4)] },
  ],
  signals: [{ bar: T(3), setup_bar: T(2), side: "long", invalidation: 97.9 }],
};
const state = (patch: Partial<TemplateOverlayState> = {}): TemplateOverlayState =>
  ({ reply: REPLY, loading: false, failed: false, sameTf: true, overlayTf: "5min", markers: [], levels: [], ...patch });
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

describe("M94 F16: the template's marks on the chart", () => {
  it("marks every bar where a part held with that part's own shape, and the newest signal's level", () => {
    const { markers, levels } = templateLayers(REPLY, CANDLES, true);
    expect(markers.map((m) => [m.timestamp, m.shape, m.text])).toEqual([
      [T(1), "circle", "S"], [T(2), "circle", "S"], [T(3), "arrowUp", "T"], [T(4), "square", "W"]]);
    expect(levels).toEqual([{ price: 97.9, title: "Wrong beyond", tone: "neutral" }]);
  });

  it("draws nothing on another timeframe or without bars, and never for a part the engine keeps", () => {
    expect(templateLayers(REPLY, CANDLES, false)).toEqual({ markers: [], levels: [] });
    expect(templateLayers({ ...REPLY, available: false }, CANDLES, true).markers).toEqual([]);
    expect(templateLayers(REPLY, CANDLES, true).markers.some((m) => m.text === "W" && m.position === "belowBar")).toBe(false);
  });

  it("gives each part and side its own mark", () => {
    expect(partMark({ part: "trigger", side: "short" })).toEqual({ shape: "arrowDown", position: "aboveBar", color: "down", text: "T" });
    expect(partMark({ part: "invalidation", side: "long" })).toMatchObject({ shape: "square", position: "belowBar", color: "down" });
    expect(partSign({ part: "setup", side: "long" })).toBe("● S");
  });
});

describe("M94 F16: the legend", () => {
  it("lists every part with its mark, its state on the last closed bar and the footer", () => {
    const html = renderToStaticMarkup(<TemplateLegend state={state()} />);
    const words = text(html);
    for (const w of ["Made-up reversal", "Setup (upward) · 2 bars in view", "Trigger (upward) · 1 bar in view",
      "Wrong beyond (upward): kept from the setup bar", "Wrong beyond (downward) · 1 bar in view", M94_FOOTER]) expect(words).toContain(w);
    expect(html).toContain('data-part="trigger" data-side="long" data-state="met"');
    expect(html).toContain('data-part="setup" data-side="long" data-state="failed"');
    expect(html).toContain('data-state="kept"');
    expect(html).toContain('aria-label="Met"');
    expect(html).toContain('aria-label="Not met"');
  });

  it("offers the template's own timeframe when the chart shows another, and says why when no bars", () => {
    expect(renderToStaticMarkup(<TemplateLegend state={state({ sameTf: false })} onShowTf={() => undefined} />)).toContain("Show the marks on");
    const none = renderToStaticMarkup(<TemplateLegend state={state({ reply: { ...REPLY, available: false, reason: "Not stored yet." } })} />);
    expect(text(none)).toContain("Not stored yet.");
    expect(renderToStaticMarkup(<TemplateLegend state={state({ reply: null, loading: true })} />)).toContain("Reading the template");
  });

  it("is on demand: the chart reads the overlay only once its toolbar button is pressed", () => {
    expect(chartsSource).toContain('const [tplShown, setTplShown] = useState(false);');
    expect(chartsSource).toContain("on: tplShown && tplAsk !== null");
    expect(chartsSource).toContain('features.on("intraday_pack") || features.on("sd_pack") ? overlayFromSearch(');   // M97 PR-3: family I
  });
});
