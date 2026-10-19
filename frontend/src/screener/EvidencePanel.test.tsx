import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EvidenceChart } from "./EvidenceChart";
import { EvidenceView, type ReplayControls } from "./EvidencePanel";
import { ResultBoard } from "./ResultBoard";
import sample from "./evidenceSample.json";
import { visibleDrawables, type EvidenceContext, type EvidencePack } from "./evidence";

const pack = sample as unknown as EvidencePack;
const none = new Set<string>();
const esc = (s: string) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

describe("EvidenceChart (U8 EV1)", () => {
  it("is an accessible image: role img, a title and a description naming what is drawn", () => {
    const html = renderToStaticMarkup(<EvidenceChart pack={pack} drawables={visibleDrawables(pack, none, false)} />);
    expect(html).toContain('role="img"');
    expect(html).toMatch(/aria-labelledby="ev-title-11 ev-desc-11"/);
    expect(html).toContain("<title id=\"ev-title-11\">Evidence chart: BRK</title>");
    expect(html).toContain("range high");
    expect(html).toContain('data-testid="evidence-matched-bar"');
    expect(html).toContain('data-testid="evidence-sub-pane"');
  });

  it("draws nothing that is not in the pack (every drawing is tagged with a pack drawable)", () => {
    const shown = visibleDrawables(pack, none, false);
    const html = renderToStaticMarkup(<EvidenceChart pack={pack} drawables={shown} />);
    const tagged = [...html.matchAll(/data-drawable="([^"]+)"/g)].map((m) => m[1]);
    expect(tagged.length).toBeGreaterThan(0);
    expect(new Set(tagged)).toEqual(new Set(shown.map((d) => d.id)));
    const bare = renderToStaticMarkup(<EvidenceChart pack={pack} drawables={[]} />);
    expect(bare).not.toContain("data-drawable");
    expect(bare).not.toContain("evidence-sub-pane");
  });

  it("uses theme tokens for neutral candles (light and dark): rising hollow, falling filled", () => {
    const html = renderToStaticMarkup(<EvidenceChart pack={pack} drawables={[]} />);
    expect(html).toContain("rgb(var(--chart-grid))");
    expect(html).toContain('fill="rgb(var(--surface-1))" stroke="rgb(var(--chart-text))"');   // rising: hollow
    expect(html).toContain('fill="rgb(var(--chart-text))" stroke="rgb(var(--chart-text))"');  // falling: filled
  });
});

describe("EvidenceView (U8 EV1)", () => {
  it("shows the blocks, the verify strip with the engine's numbers and an informational note", () => {
    const html = renderToStaticMarkup(<EvidenceView pack={pack} hiddenBlocks={none} showFailed={false} onSend={() => undefined} />);
    expect(html).toContain("Why it matched");
    for (const b of pack.blocks) expect(html).toContain(esc(b.text));
    const strip = html.slice(html.indexOf('data-testid="verify-strip"'));
    for (const r of pack.rows.filter((x) => x.passed !== false)) expect(strip).toContain(esc(r.verify));
    expect(strip).toContain("close 103.80 &gt; SMA(close, 10) 101.33");                   // the engine's numbers, as the backend wrote them
    expect(strip).not.toContain('data-row="b4.c1"');                                     // the failed condition stays hidden
    expect(html).toContain('<th scope="col"');
    expect(html).toContain('<span class="sr-only">passed</span>');
    expect(html).toContain("Show failed conditions (1)");
    expect(html).toContain("Send to my Telegram");
    expect(html).toContain("Not a recommendation.");
    expect(html).not.toMatch(/\b(buy|sell|recommend(ed|s)?)\b/i);
  });

  it("shows failed conditions with Show failed conditions on, marked for screen readers", () => {
    const html = renderToStaticMarkup(<EvidenceView pack={pack} hiddenBlocks={none} showFailed />);
    expect(html).toContain('data-row="b4.c1"');
    expect(html).toContain('<span class="sr-only">did not pass</span>');
    expect(html).not.toContain("Send to my Telegram");                                    // no handler, no button
  });

  it("keeps a Devanagari screen name exactly as the user typed it", () => {
    const named = { ...pack, screen: { ...pack.screen, name: "\u092e\u094b\u092e\u0947\u0902\u091f\u092e \u0938\u094d\u0915\u094d\u0930\u0940\u0928" } };
    const html = renderToStaticMarkup(<EvidenceView pack={named} hiddenBlocks={none} showFailed={false} />);
    expect(html).toContain("\u092e\u094b\u092e\u0947\u0902\u091f\u092e \u0938\u094d\u0915\u094d\u0930\u0940\u0928");
  });
});

describe("ResultBoard Show on chart (U8 EV1)", () => {
  it("offers Show on chart only on matched rows with evidence", () => {
    const rows = [{ symbol: "AAA", matched: true, reason: null, evidence_id: 5 }, { symbol: "BBB", matched: true, reason: null },
                  { symbol: "CCC", matched: false, reason: null }];
    const html = renderToStaticMarkup(<ResultBoard results={rows} onShowEvidence={() => undefined} />);
    expect(html.match(/data-testid="show-on-chart"/g)?.length).toBe(1);
    expect(html).toContain('aria-label="Show AAA on chart"');
    expect(renderToStaticMarkup(<ResultBoard results={rows} />)).not.toContain("show-on-chart");    // flag off: no ids, no handler
  });
});

describe("EvidenceView context and replay (U8 EV3)", () => {
  const context: EvidenceContext[] = [
    { tf: "1d", tag: "UP", expr: "DowTrend(1)", bar_close: pack.as_of.bar_close, used_by: [pack.rows[0].id], note: null, text: "1d up" },
    { tf: "1w", tag: null, expr: "DowTrend(1)@1w", bar_close: null, used_by: [], note: "not enough bars on this timeframe", text: "1w n/a" },
  ];
  const withContext = { ...pack, context } as EvidencePack;
  const replay = (over: Partial<ReplayControls> = {}): ReplayControls =>
    ({ steps: 40, index: 39, loading: false, error: null, onStep: () => undefined, onReset: () => undefined, ...over });

  it("shows the trend strip in words (not colour alone) with each chip's expression, only when the pack has one", () => {
    const html = renderToStaticMarkup(<EvidenceView pack={withContext} hiddenBlocks={none} showFailed={false} />);
    const at = html.indexOf('aria-label="Trend on other timeframes"');
    const strip = html.slice(at, html.indexOf("</section>", at));
    expect(at).toBeGreaterThan(-1);
    expect(strip).toContain('data-testid="context-strip"');
    expect(strip).toContain("1d up");
    expect(strip).toContain("1w n/a");
    expect(strip).toContain("DowTrend(1)@1w · not enough bars on this timeframe");
    expect(strip).toContain("(read by the screen)");
    expect(renderToStaticMarkup(<EvidenceView pack={pack} hiddenBlocks={none} showFailed={false} />)).not.toContain("context-strip");
  });

  it("offers replay controls: at the match only step back; earlier, forward and back to the match", () => {
    const atMatch = renderToStaticMarkup(<EvidenceView pack={withContext} hiddenBlocks={none} showFailed={false} replay={replay()} />);
    expect(atMatch).toContain("bar 40 of 40 (the match)");
    expect(atMatch).toMatch(/aria-label="Step forward one bar"[^>]*disabled=""|disabled=""[^>]*aria-label="Step forward one bar"/);
    expect(atMatch).not.toContain("Back to the match");
    const earlier = { ...withContext, can_send: false,
                      replay: { at: pack.as_of.bar_close, source: "lake:1d", note: "Replayed from the shared market-data history.", stored_as_of: pack.as_of } };
    const html = renderToStaticMarkup(<EvidenceView pack={earlier} hiddenBlocks={none} showFailed={false} onSend={() => undefined} replay={replay({ index: 10 })} />);
    expect(html).toContain("bar 11 of 40");
    expect(html).toContain("Back to the match");
    expect(html).toContain("Replayed as of the bar of");
    expect(html).not.toContain("Send to my Telegram");                                   // a replay is never sent
    expect(renderToStaticMarkup(<EvidenceView pack={earlier} hiddenBlocks={none} showFailed={false} replay={replay({ index: 10, loading: true })} />))
      .toContain("Replaying…");
    expect(renderToStaticMarkup(<EvidenceView pack={withContext} hiddenBlocks={none} showFailed={false} replay={replay({ index: 3, error: "Replay is not available for this evidence." })} />))
      .toContain('role="alert" class="text-warn">Replay is not available');
    expect(renderToStaticMarkup(<EvidenceView pack={pack} hiddenBlocks={none} showFailed={false} />)).not.toContain("replay-bar");
  });
});
