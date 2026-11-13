import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import panelsSource from "../terminal/PanelsV2.tsx?raw";

/** Owner report 13 Nov ("the drawing toolkit on the chart - where did it go?"): the Terminal's chart panel shows the
 * Charts page's own drawing tools - the foldable rail from 768 px, the one Draw menu on a phone - on the same drawings. */
vi.mock("lightweight-charts", () => import("./lightweightFake.fixture"));
const { default: EmbeddedChartV2 } = await import("./EmbeddedChartV2");
const { ChartPanelV2 } = await import("../terminal/PanelsV2");
const { AuthProvider } = await import("../auth/AuthContext");

const render = (node: React.ReactNode) => renderToStaticMarkup(<AuthProvider><MemoryRouter>{node}</MemoryRouter></AuthProvider>);

describe("drawing tools on the Terminal chart", () => {
  it("the Terminal's chart panel renders the drawing rail and the phone's Draw menu", () => {
    const html = render(<ChartPanelV2 symbol="DEMO 50" />);
    expect(html).toContain('data-embedded-chart="DEMO 50"');
    expect(html).toContain("data-drawing-rail-open");                       // the rail, open by default
    expect(html).toContain('aria-label="Fold drawing tools"');
    for (const tool of ["Trend line", "Horizontal line"]) expect(html).toContain(`aria-label="${tool}"`);
    expect(html).toMatch(/<button[^>]*aria-label="Drawing tools"[^>]*aria-haspopup="dialog"/);   // the phone's one Draw button
    expect(panelsSource).toMatch(/<EmbeddedChartV2 [^>]*\bdrawing \/>/);
  });

  it("another embedded chart keeps no drawing tools (off by default, as before)", () => {
    const html = render(<EmbeddedChartV2 symbol="DEMO 50" />);
    expect(html).not.toContain("data-drawing-rail");
    expect(html).not.toContain('aria-label="Drawing tools"');
  });
});
