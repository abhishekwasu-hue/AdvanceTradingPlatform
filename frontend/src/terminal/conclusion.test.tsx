import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ConclusionViewV2, SummaryV2, summaryLines } from "./ConclusionPanelV2";
import { OI_SLOT_MS, SETTLE_MS, chipLabel, costText, strategyTimeframes, closedIndex, conclusionPath, glyph, msToNext, segments, tfMs, toneOf, type Conclusion } from "./conclusion";
import { CHOOSER_PANEL_IDS, PANEL_IDS, PRESETS, layoutProblem, phoneTabs, presetLayout, withConclusion, withoutConclusion } from "./layoutModel";
import { callWords } from "../signals/language";
import pageSource from "./TerminalPageV2.tsx?raw";
import panelSource from "./ConclusionPanelV2.tsx?raw";

const BODY: Conclusion = {
  symbol: "NIFTY", expiry: "2026-11-26", tf: "15m", as_of: "2026-11-20T05:00:00+00:00", fingerprint: "f1",
  note: "Information only: what ATP's readings show, never a recommendation. The decision is yours.",
  readings: [
    { source: "chart", label: "15-minute structure", tf: "15m", direction: "up", as_of: null },
    { source: "oi", label: "OI", tf: null, direction: "up", as_of: null, stale: true },
    { source: "signals", label: "Your signals", tf: null, direction: "up", as_of: null },
    { source: "positions", label: "Positions", tf: null, direction: "down", as_of: null },
  ],
  needs_you: [{ key: "orders", text: "1 order not placed today", action: "orders" }],
  glance: {
    lean: { side: "up", count: 3, of: 4, words: "Lean up · 3 of 4" },
    meter: { up: 3, down: 1, range: 0, none: 0, total: 4 },
    agrees: [{ direction: "up", text: "15-minute structure up · OI up · Your signals up", sources: ["chart", "oi", "signals"] }],
    conflicts: [{ text: "15-minute structure up · OI up · Your signals up · Positions down", sources: [] }],
    line: "15-minute structure up · OI up · Your signals up · Positions down",
  },
  sections: [
    { key: "chart", title: "Chart", status: "ok", source: "Chart reading (closed bars)", as_of: null, note: null,
      lines: [{ text: "Buyer area: 24,180–24,220.", tone: "up", kind: "signal", source: "Stored areas", as_of: null }] },
    { key: "oi", title: "OI", status: "stale", source: "OI banner", as_of: null, note: "Last OI reading 12 minutes ago.", lines: [] },
  ],
};

describe("TERM-CON C2: the Conclusion panel", () => {
  it("shows the glance strip: lean cue with its count, the meter with glyphs, chips per source, the line, what needs him", () => {
    const html = renderToStaticMarkup(<ConclusionViewV2 data={BODY} state="ok" />);
    expect(html).toContain('data-lean="up"');
    expect(html).toContain("Lean up · 3 of 4");
    expect((html.match(/data-segment="up"/g) ?? []).length).toBe(3);
    expect((html.match(/data-segment="down"/g) ?? []).length).toBe(1);
    expect(html).toContain("3 up · 1 down · 0 range");
    for (const s of ["chart", "oi", "signals", "positions", "orders"]) expect(html).toContain(`data-chip-source="${s}"`);
    expect(html).toContain('data-chip-source="orders" data-chip-direction="warn"');
    expect(html).toContain("border-dashed");                                   // the stale OI chip
    expect(html).not.toContain("data-glance-line");                           // the line equals the conflict text: shown once
    const calm = renderToStaticMarkup(<ConclusionViewV2 data={{ ...BODY, glance: { ...BODY.glance, conflicts: [] } }} state="ok" />);
    expect(calm).toContain("data-glance-line");
    expect(html).toContain('data-need="orders"');
    expect(html).toContain('data-section="oi" data-section-status="stale"');
  });

  it("never carries a buy / sell / target / stop word of its own and never colour alone", () => {
    const html = renderToStaticMarkup(<ConclusionViewV2 data={BODY} state="ok" />);
    const text = html.replace(/<[^>]+>/g, " ");
    expect(callWords(text)).toEqual([]);
    expect(glyph("up")).toBe("▲"); expect(glyph("down")).toBe("▼"); expect(glyph("range")).toBe("◆"); expect(glyph("none")).toBe("—");
    expect(toneOf("up").text).toBe("text-up"); expect(toneOf("down").text).toBe("text-down"); expect(toneOf("range").text).toBe("text-fg-muted");
    expect(panelSource).not.toMatch(/#[0-9a-fA-F]{3,6}\b|text-\[|bg-\[/);       // tokens only
    expect(panelSource).not.toMatch(/placeOrder|place_order|submitOrder/);       // read only
  });

  it("says what it is doing while loading or when the read failed", () => {
    expect(renderToStaticMarkup(<ConclusionViewV2 data={null} state="loading" />)).toContain("Reading the chart, OI and your positions");
    expect(renderToStaticMarkup(<ConclusionViewV2 data={null} state="error" />)).toContain("We could not read the conclusion right now");
  });

  it("helpers: the path, the chip labels, the meter's segments", () => {
    expect(conclusionPath("NIFTY", "2026-11-26", "15m", ["1h", "15m", "1d", "5m"])).toBe(
      "/terminal/conclusion?symbol=NIFTY&tf=15m&expiry=2026-11-26&also=1h&also=1d");
    expect(chipLabel(BODY.readings[0])).toBe("15m");
    expect(chipLabel(BODY.readings[2])).toBe("Strategy");
    expect(segments({ up: 1, down: 1, range: 1, none: 1, total: 4 })).toEqual(["up", "down", "range", "none"]);
  });

  it("the layout: a panel id, opened on Option seller and Quick trade while the flag is on, never in a saved layout, a phone tab after Chart", () => {
    expect(PANEL_IDS).toContain("conclusion");
    expect(CHOOSER_PANEL_IDS).toContain("conclusion");
    for (const id of ["option_seller", "quick_trade"] as const) {
      expect(PRESETS[id].panels.some((x) => x.id === "conclusion")).toBe(false);          // the presets' own geometry is unchanged
      const shown = withConclusion(presetLayout(id).panels, id, true);
      expect(shown.find((x) => x.id === "conclusion")?.open).toBe(true);
      expect(layoutProblem(shown)).toBeNull();
      expect(withoutConclusion(shown).find((x) => x.id === "conclusion")?.open).toBe(false);
      expect(withConclusion(presetLayout(id).panels, id, false).some((x) => x.id === "conclusion")).toBe(false);   // his own layout
    }
    expect(withConclusion(presetLayout("option_buyer").panels, "option_buyer", true).some((x) => x.id === "conclusion")).toBe(false);
    expect(phoneTabs(false, true).map((t) => t.key).slice(0, 2)).toEqual(["chart", "conclusion"]);
    expect(phoneTabs(false).map((t) => t.key)).not.toContain("conclusion");
    expect(pageSource).toMatch(/features\.on\("terminal_conclusion"\)/);
    expect(pageSource).toMatch(/hide=\{conclusionOn \? undefined : "conclusion"\}/);
  });

  it("C3: one timer to the next bar close (NSE bars from 09:15 IST) and the next OI slot - never an interval", () => {
    const at = (iso: string) => Date.parse(iso);
    const q = tfMs("15m");
    expect(tfMs("1h")).toBe(3_600_000); expect(tfMs("5m")).toBe(300_000); expect(tfMs("1d")).toBe(86_400_000);
    // 10:29:59 IST: the 10:15 bar is still forming; 10:30:05 IST (after the settle) it has closed.
    const before = closedIndex(at("2026-11-20T04:59:59Z"), q);
    expect(closedIndex(at("2026-11-20T05:00:05Z"), q)).toBe(before + 1);
    expect(msToNext(at("2026-11-20T04:59:59Z"), q)).toBe(1_000 + SETTLE_MS);
    // a 1-hour bar closes at 10:15 IST (09:15 + 1 h), not at 10:00
    expect(msToNext(at("2026-11-20T04:30:00Z"), tfMs("1h"))).toBe(15 * 60_000 + SETTLE_MS);
    expect(msToNext(at("2026-11-20T04:31:00Z"), OI_SLOT_MS)).toBe(4 * 60_000 + SETTLE_MS);
    expect(conclusionPath("NIFTY", null, "15m", [], "a|b|3")).toBe("/terminal/conclusion?symbol=NIFTY&tf=15m&v=a%7Cb%7C3");
    expect(pageSource).not.toMatch(/setInterval/);
    expect(pageSource).toMatch(/useStepClose\(tfMs\(mainTf \?\? "15m"\), conclusionLive\)/);
  });

  it("C4: Summarise on tap; the summary with its cost; no key or refused: a plain reason, the readings unchanged", () => {
    const ask = renderToStaticMarkup(<SummaryV2 busy={false} result={null} failed={false} onAsk={() => undefined} />);
    expect(ask).toContain("Summarise with AI");
    const done = renderToStaticMarkup(<SummaryV2 busy={false} failed={false} onAsk={() => undefined}
      result={{ ready: true, text: "- Most readings lean up\n- Expiry in 2 sessions", reason: null, usage: { calls: 1, tokens_input: 900, tokens_output: 80, cost_inr: 0.42 } }} />);
    expect(done).toContain("Most readings lean up");
    expect((done.match(/<li/g) ?? []).length).toBe(2);                      // owner 21 Nov: a glance, one line per point
    expect(done).toContain("AI · information only · ₹0.42 on your AI key");
    expect(summaryLines("- a\n- b\n- c\n- d")).toEqual(["a", "b", "c"]);
    expect(summaryLines("One plain reply.")).toEqual(["One plain reply."]);
    const nokey = renderToStaticMarkup(<SummaryV2 busy={false} failed={false} onAsk={() => undefined}
      result={{ ready: false, text: null, reason: "Connect your own AI key in Settings to get a summary.", usage: null }} />);
    expect(nokey).toContain("Connect your own AI key");
    expect(nokey).toContain("Summarise with AI");
    expect(costText(null)).toBe("");
    expect(renderToStaticMarkup(<ConclusionViewV2 data={BODY} state="ok" summary={<i data-x />} />)).toContain("data-x");
  });

  it("TC-Q2: the timeframes of his ACTIVE / PAUSED bots on this symbol, besides the chart's, at most two", () => {
    const bot = (symbol: string, timeframe: string, status = "ACTIVE") => ({ symbol, timeframe, status });
    const bots = [bot("NIFTY 50", "60min"), bot("NIFTY25NOV25800CE", "5min", "PAUSED"), bot("NIFTY", "15min"), bot("NIFTY", "day"),
      bot("BANKNIFTY", "30min"), bot("NIFTYNXT50", "3min"), bot("NIFTY", "1min", "STOPPED"), bot("NIFTY", "week")];
    expect(strategyTimeframes(bots, "NIFTY", "NIFTY 50", "15m")).toEqual(["1h", "5m"]);       // 15m = the chart's; at most two
    expect(strategyTimeframes(bots.slice(4), "NIFTY", "NIFTY 50", "15m")).toEqual([]);        // other symbols, stopped, no read TF
    expect(strategyTimeframes([], "NIFTY", "NIFTY 50", "15m")).toEqual([]);
    expect(pageSource).toMatch(/also=\{conclusionAlso\}/);
  });
});
