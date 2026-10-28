import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChartFrame, LegendItem } from "./ChartFrame";
import { DISCLAIMER_TEXT, Disclaimer } from "./Disclaimer";
import { HelpPopover } from "./HelpPopover";
import { PageHeader } from "./PageHeader";
import { ResponsiveTable } from "./ResponsiveTable";
import { StatusBadge, asOfText } from "./StatusBadge";
import { SummaryCard } from "./SummaryCard";
import { UnavailableState } from "./UnavailableState";

describe("UI-H Wave 0: the shared page parts", () => {
  it("PageHeader (H1, H9): one h1, never truncated; a short title on phones keeps the full name", () => {
    const html = renderToStaticMarkup(<PageHeader title="Sectors & indices" short="Sectors" line="How each part of the market moved." />);
    expect(html.match(/<h1/g)).toHaveLength(1);
    expect(html).not.toMatch(/truncate|text-ellipsis|line-clamp/);
    expect(html).toContain("break-words");
    expect(html).toContain('aria-label="Sectors &amp; indices"');
    expect(html).toContain('<span class="md:hidden">Sectors</span>');
  });

  it("StatusBadge (H7): one chip; closed reads 'as of' the last close in IST", () => {
    const now = new Date("2026-10-24T08:09:00Z");                                         // Sat 13:39 IST
    expect(asOfText("2026-10-23T10:00:00Z", now)).toBe("as of 23 Oct 2026, 15:30 IST");
    expect(asOfText("2026-10-24T05:00:00Z", now)).toBe("as of 10:30 IST");
    expect(asOfText(null, now)).toBe("");
    const html = renderToStaticMarkup(<StatusBadge state="closed" asOf="2026-10-23T10:00:00Z" now={now} />);
    expect(html).toContain("Closed · as of 23 Oct 2026, 15:30 IST");
    expect(html).toContain('data-status-badge="closed"');
    expect(renderToStaticMarkup(<StatusBadge state="live" />)).toContain("Live");
  });

  it("SummaryCard: a label, one big number, one plain line", () => {
    const html = renderToStaticMarkup(<SummaryCard label="Open P&L" value="+₹3,720" line="Across 3 positions." />);
    expect(html).toContain('aria-label="Open P&amp;L"');
    expect(html).toContain("text-t24");
    expect(html).toContain("Across 3 positions.");
    expect(html).not.toContain("uppercase");
  });

  it("HelpPopover (H3): a (?) button, closed at first, named after its topic", () => {
    const html = renderToStaticMarkup(<HelpPopover title="How max pain is worked out">Text</HelpPopover>);
    expect(html).toMatch(/<button[^>]*aria-label="Help: How max pain is worked out"[^>]*aria-expanded="false"[^>]*aria-haspopup="dialog"/);
    expect(html).not.toContain("Text");                                                    // the method text is behind the (?)
  });

  it("ChartFrame (H6, C1): the empty state sits ON TOP of the canvas, which stays mounted", () => {
    const html = renderToStaticMarkup(
      <ChartFrame title="Max pain" empty="No bars for this contract yet." legend={<LegendItem swatch="bg-oi-call">Calls</LegendItem>}
        axis="Strike prices along the bottom.">
        <canvas data-test-canvas />
      </ChartFrame>,
    );
    expect(html).toContain("data-test-canvas");
    expect(html).toMatch(/data-chart-canvas[^>]*aria-hidden="true"/);
    const canvasAt = html.indexOf("data-chart-canvas");
    const emptyAt = html.indexOf("data-chart-empty");
    expect(emptyAt).toBeGreaterThan(canvasAt);                                              // later in the DOM: painted above
    expect(html).toMatch(/role="status" data-chart-empty="true" class="absolute inset-0 z-10/);
    expect(html).toContain("Calls");
    expect(html).toContain("Strike prices along the bottom.");
    const full = renderToStaticMarkup(<ChartFrame title="Max pain"><canvas /></ChartFrame>);
    expect(full).not.toContain("data-chart-empty");
  });

  it("ResponsiveTable (H5): sticky first column, scroll inside the box; a Show table toggle when collapsible", () => {
    const cols = [{ key: "s", header: "Strike", cell: (r: number) => r }, { key: "oi", header: "OI", align: "right" as const, cell: (r: number) => r * 2 }];
    const html = renderToStaticMarkup(<ResponsiveTable caption="Max pain by strike" columns={cols} rows={[1, 2]} rowKey={(r) => String(r)} />);
    expect(html).toContain("overflow-x-auto");
    expect(html).toMatch(/<th scope="col" class="[^"]*sticky left-0/);
    expect(html).toMatch(/<th scope="row" class="sticky left-0/);
    expect(html).toContain("<caption");
    const closed = renderToStaticMarkup(<ResponsiveTable caption="X" columns={cols} rows={[1]} rowKey={(r) => String(r)} collapsible />);
    expect(closed).toMatch(/aria-expanded="false"[^>]*>Show table</);
    expect(closed).not.toContain("<table");
  });

  it("Disclaimer (H10): one wording", () => {
    expect(DISCLAIMER_TEXT).toBe("For information and education: every trading decision is yours.");
    expect(renderToStaticMarkup(<Disclaimer />)).toContain(DISCLAIMER_TEXT);
  });

  it("UnavailableState (H8): the user's sentence; no admin detail without an admin session", () => {
    const html = renderToStaticMarkup(
      <UnavailableState title="Chart reading" message="Chart reading is being set up on our server." adminDetail="atp_private.chart_analyze is MISSING" />,
    );
    expect(html).toContain("Chart reading is being set up on our server.");
    expect(html).not.toContain("MISSING");
    expect(html).not.toMatch(/Coming soon/i);
    const admin = renderToStaticMarkup(<UnavailableState isAdmin title="Chart reading" message="m" adminDetail="engine MISSING" />);
    expect(admin).toContain("For admins: </span>engine MISSING");
  });
});
