import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import panelsSource from "../terminal/PanelsV2.tsx?raw";
import positionSource from "./PositionChartV2.tsx?raw";

/** Owner report 13 Nov ("the drawing toolkit on the chart - where did it go?"): the Terminal's chart panel shows the
 * Charts page's own drawing tools - the foldable rail from 768 px, the one Draw menu on a phone - on the same drawings. */
vi.mock("lightweight-charts", () => import("./lightweightFake.fixture"));
const { default: EmbeddedChartV2 } = await import("./EmbeddedChartV2");
const { ChartPanelV2 } = await import("../terminal/PanelsV2");
const { AuthProvider } = await import("../auth/AuthContext");
const { default: ChartPanel } = await import("../inplace/panels/ChartPanel");

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

  it("owner 14 Nov: every chart window has them - on by default, the Draw menu over the chart itself without a bar", () => {
    const html = render(<EmbeddedChartV2 symbol="DEMO 50" />);
    expect(html).toContain("data-drawing-rail-open");
    expect(html).toMatch(/<button[^>]*aria-label="Drawing tools"[^>]*aria-haspopup="dialog"/);
    expect(html).not.toContain("data-drawing-menu-own");                 // the bar's own Draw menu, not a second one
    const bare = render(<EmbeddedChartV2 symbol="DEMO 50" toolbar={false} />);   // the lower chart of a CE / PE pair
    expect(bare).not.toContain('aria-label="Chart controls for DEMO 50"');
    expect(bare).toContain("data-drawing-menu-own");
    expect(bare).toContain("data-drawing-rail-open");
  });

  it("the in-place chart sheet: one chart and both charts of a CE / PE pair carry the tools (each its own symbol)", () => {
    const one = render(<ChartPanel params={{ symbol: "DEMO 50" }} replace={() => {}} close={() => {}} />);
    expect(one).toContain("data-drawing-rail-open");
    const pair = render(<ChartPanel params={{ symbol: "NIFTY 25050 CE 17 NOV 26", exchange: "NFO" }} replace={() => {}} close={() => {}} />);
    expect(pair.match(/data-drawing-rail-open/g)).toHaveLength(2);
    expect(pair.match(/<button[^>]*aria-label="Drawing tools"[^>]*aria-haspopup="dialog"/g)).toHaveLength(2);    // the bar's Draw menu (CE) and the PE chart's own
    for (const sym of ["NIFTY 25050 CE 17 NOV 26", "NIFTY 25050 PE 17 NOV 26"]) expect(pair).toContain(`data-embedded-chart="${sym}"`);
    expect(positionSource).not.toMatch(/drawing=\{false\}/);              // the position chart keeps the default (on)
  });

  it("opted out only where the chart is not a window of its own: a chat answer's preview and a reading figure", () => {
    const html = render(<EmbeddedChartV2 symbol="DEMO 50" drawing={false} />);
    expect(html).not.toContain("data-drawing-rail");
    expect(html).not.toContain('aria-label="Drawing tools"');
    const optedOut = Object.entries(import.meta.glob("../**/*.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>)
      .filter(([file, src]) => !file.includes(".test.") && /<EmbeddedChartV2\b[^>]*drawing=\{false\}/.test(src)).map(([file]) => file).sort();
    expect(optedOut).toEqual(["../chartAnalyze/HybridResultV2.tsx", "../copilot/chat/AnswerV2.tsx"]);
  });
});
