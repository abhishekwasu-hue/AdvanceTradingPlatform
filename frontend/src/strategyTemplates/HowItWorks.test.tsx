import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import h6Sample from "./howItWorksSample.json";
import h8Sample from "./howItWorksSampleH8.json";
import { M94_FOOTER, type HowItWorks } from "./api";
import { HowItWorksView } from "./HowItWorks";
import {
  chartPathFor, howPathFor, overlayFromSearch, scannerPathFor, templateIdOf, templateLinkFromSearch, templateScanFromSearch,
} from "./link";
import { schematicMarks, schematicY } from "./Schematic";
import { TemplateLibraryView, hasHowItWorks } from "./TemplateLibrarySection";

/** M94 F15: a template's "how it works" screen, on the server's own answers (backend tests/test_m94_pr6_pages.py checks
 * both samples are what the route serves). */
const H6 = h6Sample as unknown as HowItWorks;      // the setup event is the signal (one bar)
const H8 = h8Sample as unknown as HowItWorks;      // a setup, then its trigger
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");
const render = (how: HowItWorks) => renderToStaticMarkup(<HowItWorksView how={how} />);

describe("M94 F15: the how it works screen", () => {
  it("shows 3-5 short lines from the template's own data, in order, with the footer", () => {
    for (const how of [H6, H8]) {
      const html = render(how);
      expect(how.lines.length).toBeGreaterThanOrEqual(3);
      expect(how.lines.length).toBeLessThanOrEqual(5);
      const order = how.lines.map((l) => html.indexOf(`data-how-line="${l.key}"`));
      expect(order.every((v, i) => v > 0 && (i === 0 || v > order[i - 1]))).toBe(true);
      for (const l of how.lines) expect(text(html)).toContain(l.text.replace(/'/g, "&#x27;"));
      expect(how.footer).toBe(M94_FOOTER);
      expect(text(html.split("data-m94-footer")[1])).toContain(M94_FOOTER);
      expect(text(html)).toContain("Reference values - edit before use");
    }
  });

  it("draws the schematic with the setup, the trigger and the invalidation marked, and no number in words", () => {
    const one = render(H6);
    expect(one).toContain('data-mark="setup"');                       // setup and trigger on one bar: one mark
    expect(one).not.toContain('data-mark="trigger"');
    expect(one).toContain('data-mark="invalidation"');
    expect(text(one)).toContain("Setup and trigger on the same bar (upward idea)");
    const two = render(H8);
    for (const m of ["setup", "trigger", "invalidation"]) expect(two).toContain(`data-mark="${m}"`);
    expect(text(two)).toContain("Where the idea is wrong");
    expect((two.match(/data-candle=/g) ?? []).length).toBe(H8.schematic!.candles.length);
    // the drawing's words carry no price: the caption and the legend have no digit except the timeframe's
    const caption = text(`<x${two.split("<figcaption")[1].split("</figcaption>")[0]}`);
    expect(/\d/.test(caption)).toBe(false);
  });

  it("puts the invalidation line on the losing side of the trigger close (the engine's own geometry)", () => {
    for (const how of [H6, H8]) {
      const s = how.schematic!;
      const close = s.candles[s.trigger][3];
      const inv = schematicY(s.invalidation, s.scale);
      if (s.side === "long") expect(inv).toBeGreaterThan(schematicY(close, s.scale));   // lower on screen
      else expect(inv).toBeLessThan(schematicY(close, s.scale));
    }
    expect(schematicMarks({ same_bar: false, side: "short" }).map((m) => m.label)).toEqual(["Setup", "Trigger (downward idea)", "Where the idea is wrong"]);
  });

  it("links the setup scanner per side, the chart overlay and the trading bot; the settings are folded", () => {
    const html = render(H6);
    expect(html).toContain('href="/screener?template=H6&amp;side=long"');
    expect(html).toContain('href="/screener?template=H6&amp;side=short"');
    expect(text(html)).toContain("Setup scanner (upward)");
    expect(html).toContain('href="/charts?tpl=H6&amp;tpltf=5m"');
    expect(html).toContain('href="/deployments?template=H6&amp;timeframe=5m"');
    const fold = html.split('data-library-fold="how-settings"')[1];
    expect(html.split('data-library-fold="how-settings"')[0].endsWith("<details ")).toBe(true);     // folded: a closed <details>
    expect(html.split('data-library-fold="how-settings"')[0].slice(-30)).not.toContain("open");
    expect(text(fold)).toContain("Opening range (minutes)");
    expect(text(fold)).toContain("Only between: Not set");
  });

  it("a template not switched on has the line and no links", () => {
    const html = render({ ...H6, template: { ...H6.template, available: false } });
    expect(text(html)).toContain("Not switched on for your account yet");
    expect(html).not.toContain("data-how-scanner");
    expect(html).not.toContain("data-how-chart");
  });
});

describe("M94 F15: reached from the template card", () => {
  const card = (on: boolean) => renderToStaticMarkup(<TemplateLibraryView templates={[H6.template, { ...H6.template, id: "D1", family: "D", family_name: "Trend following" }]}
    error={null} family="all" onFamily={() => undefined} intradayPackOn={on} />);
  it("shows How it works on a family-H card only while intraday_pack is on", () => {
    const on = card(true);
    expect(on).toContain('data-how-link="H6"');
    expect(on).toContain('href="/strategies?how=H6"');
    expect(on).not.toContain('data-how-link="D1"');
    expect(card(false)).not.toContain("data-how-link");
    expect(hasHowItWorks({ family: "H" }, true)).toBe(true);
    expect(hasHowItWorks({ family: "H" }, false)).toBe(false);
  });
});

describe("M94: the template addresses", () => {
  it("reads and writes the screen, the scanner and the overlay addresses; unknown shapes are dropped", () => {
    expect(howPathFor("H10")).toBe("/strategies?how=H10");
    expect(scannerPathFor("H6", "short")).toBe("/screener?template=H6&side=short");
    expect(templateScanFromSearch("?template=H6&side=long")).toEqual({ template: "H6", side: "long" });
    expect(templateScanFromSearch("?template=H6&side=up")).toBeNull();
    expect(templateScanFromSearch("?template=Z6&side=long")).toBeNull();
    expect(chartPathFor("H6", "5m")).toBe("/charts?tpl=H6&tpltf=5m");
    expect(overlayFromSearch("?tpl=H6&tpltf=5m")).toEqual({ template: "H6", timeframe: "5m" });
    expect(overlayFromSearch("?tpl=H6&tpltf=bad")).toEqual({ template: "H6", timeframe: null });
    expect(overlayFromSearch("?tpl=<x>")).toBeNull();
    expect(templateIdOf("H6")).toBe("H6");
    // a family-H id is a template link too (the trading bot form picks it)
    expect(templateLinkFromSearch("?template=H6&timeframe=5m")?.template).toBe("H6");
  });
});
