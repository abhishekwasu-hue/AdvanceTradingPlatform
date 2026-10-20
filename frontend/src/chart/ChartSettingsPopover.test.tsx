import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChartSettingsPopover } from "./ChartSettingsPopover";
import { DEFAULT_CHART_PREFS } from "./useChartPrefs";

const render = (prefs = DEFAULT_CHART_PREFS, extra: { error?: string } = {}) =>
  renderToStaticMarkup(<ChartSettingsPopover prefs={prefs} onChange={() => {}} defaultOpen {...extra} />);

describe("UI2-4b1 chart settings popover", () => {
  it("closed: one labelled button", () => {
    const html = renderToStaticMarkup(<ChartSettingsPopover prefs={DEFAULT_CHART_PREFS} onChange={() => {}} />);
    expect(html).toContain('aria-label="Chart settings"');
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain('role="dialog"');
  });

  it("theme App / Light / Dark, the grid switch and the candle colours, with the current choice checked", () => {
    const html = render();
    expect(html.match(/role="radio"/g)).toHaveLength(6);
    expect(html).toMatch(/aria-checked="true"[^>]*>App</);
    expect(html).toMatch(/role="switch"(?![^>]*checked=)/);                     // grid off
    expect(html).toContain("Green / red");
    expect(html).toContain("rgb(var(--chart-up))");                             // the theme's own colours
    expect(html).toMatch(/Reset colours/);
    expect(html).toMatch(/disabled=""[^>]*>Reset colours/);                     // nothing to reset
  });

  it("a preset and the grid show as chosen; reset is offered; a save problem is shown in plain words", () => {
    const html = render({ ...DEFAULT_CHART_PREFS, theme: "dark", grid: true, candle_colors: { up: "#0d9488", down: "#ea580c" } },
                        { error: "Chart settings were not saved. Check your connection and try again." });
    expect(html).toMatch(/aria-checked="true"[^>]*>Dark</);
    expect(html).toMatch(/role="switch"[^>]*checked=""/);
    expect(html).toMatch(/aria-checked="true"[^>]*><span[^>]*background-color:#0d9488/);
    expect(html).not.toMatch(/disabled=""[^>]*>Reset colours/);
    expect(html).toContain("Chart settings were not saved.");
  });
});
