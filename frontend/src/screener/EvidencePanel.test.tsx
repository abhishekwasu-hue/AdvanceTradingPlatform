import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EvidenceChart } from "./EvidenceChart";
import { EvidenceView } from "./EvidencePanel";
import { ResultBoard } from "./ResultBoard";
import sample from "./evidenceSample.json";
import { visibleDrawables, type EvidencePack } from "./evidence";

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
    const named = { ...pack, screen: { ...pack.screen, name: "मोमेंटम स्क्रीन" } };
    const html = renderToStaticMarkup(<EvidenceView pack={named} hiddenBlocks={none} showFailed={false} />);
    expect(html).toContain("मोमेंटम स्क्रीन");
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
