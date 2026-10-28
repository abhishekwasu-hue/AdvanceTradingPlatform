import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import contract from "./builderTexts.json";
import { EvidenceView } from "./EvidencePanel";
import evidenceSample from "./evidenceSample.json";
import { plainCondition, type EvidencePack } from "./evidence";
import { FunnelCanvas } from "./FunnelCanvas";
import { newBlock, newStage, TIMEFRAMES, type GroupStage, type Registry, type Stage } from "./model";
import { TextAreaInline, TextInline } from "./ParamInline";
import { ResultBoard } from "./ResultBoard";
import { NIFTY50_FALLBACK_AS_OF, NIFTY50_FALLBACK_NOTE } from "./universe";

/**
 * Owner order SCR-FIX, PR D (phone UI, D1-D10): the Find setups page on a phone. Each check names its item; the
 * Playwright specs (e2e/scan-universe-v2, screener-idea-v2, screener-versions-v2) check the same on a real 390 px page.
 */
const reg = contract.registry as unknown as Registry;
const read = (path: string) => readFileSync(path, "utf8");
const textOf = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

function canvas(stages: Stage[]): string {
  return renderToStaticMarkup(
    <FunnelCanvas stages={stages} registry={reg} scanTf="1d" universe={50} funnel={null} fresh={false} matched={12} problems={{}} onChange={() => undefined} formula />,
  );
}

describe("SCR-FIX PR D: Find setups on a phone", () => {
  it("D1: Run and Save are 44 px, Save has its word, Run is outlined and says why it is off", () => {
    const page = read("src/pages/ScreenerPage.tsx");
    const bar = page.slice(page.indexOf('data-testid="run-why-mobile"'));
    expect(bar).toContain('<Button size="v2" onClick={() => void doSave()} disabled={!text}><Save size={16} aria-hidden /> Save</Button>');
    expect(bar).toContain('<Button size="v2" variant="primary" edge onClick={() => void doRun()} disabled={!canRun} loading={running} data-testid="run-scan-mobile"');
    expect(page).toContain('<Button size="v2" variant="primary" edge onClick={() => void doRun()} disabled={!canRun} loading={running} data-testid="run-scan"\n');
    expect(page).toContain('aria-describedby={runWhy ? "run-why-mobile" : undefined}');
    expect(page).toContain("runBlocker({ stages, symbols: symbols.length, text, problems: allProblems, otherProblem })");
  });

  it("D2: a long value never runs past the stage - the formula wraps, inputs stop at the line, no fixed minimum on a phone", () => {
    const area = renderToStaticMarkup(<TextAreaInline value={"close > Max(high, 20)[1] AND ".repeat(4)} label="Formula" onChange={() => undefined} />);
    expect(area).toMatch(/^<textarea[^>]*class="[^"]*\bw-full min-w-0 max-w-full\b/);
    expect(area).toContain("break-words");
    const input = renderToStaticMarkup(<TextInline value="NIFTY BANK" label="Index" onChange={() => undefined} />);
    expect(input).toMatch(/class="min-w-0 max-w-full /);
    const html = canvas([{ id: "f1", kind: "formula", text: "high - low < Min(high - low, 6)[1]", enabled: true }]);
    expect(html).toContain('aria-label="Formula condition in ScreenQL"');
    expect(html).toContain("<textarea");
    expect(html).not.toContain("min-w-[14rem] flex-1");                                   // the old phone minimum
    expect(html).toContain("sm:min-w-[14rem]");
    expect(read("src/shell/AppShellV2.tsx")).toContain('key === "copilot" || key === "screener" ? "overflow-x-clip"');
  });

  it("D3: the footer wraps, so the Matched count stays in the card", () => {
    const html = canvas([newStage(reg)]);
    const foot = html.slice(html.lastIndexOf("Add a condition") - 900);
    expect(foot).toContain("flex flex-wrap items-center justify-between");
    expect(textOf(foot)).toContain("Matched 12");
  });

  it("D4: the brackets read across in words; a NOT group says what it compiles to (not all of)", () => {
    const not: GroupStage = { id: "g1", kind: "group", op: "NOT", children: [newStage(reg), newBlock(reg, "filter")], enabled: true };
    const any: GroupStage = { ...not, id: "g2", op: "ANY" };
    const html = canvas([newStage(reg), not, any]);
    expect(html).not.toContain("rotate-90");
    const words = textOf(html);
    expect(words).toContain("All of these must pass");
    expect(words).toContain("Not all of these: passes unless every one passes");
    expect(words).toContain("Any of these must pass");
    expect(words).toContain("Not all of these");                                          // the group's own picker
    expect(words).not.toMatch(/None of these/);
  });

  it("D5: results keep the symbol on one line and in view, the result wraps, the stage dots stack under it on a phone", () => {
    const html = renderToStaticMarkup(
      <ResultBoard results={[{ symbol: "BAJAJ-AUTO", matched: true, reason: null }, { symbol: "TCS", matched: false, reason: "not enough bars for RSI(14) on the daily chart" }]}
                   why={() => [{ id: "a", summary: "RSI(14) above 60", passed: true }]} />,
    );
    expect(html).toContain("table-fixed");
    expect(html).toMatch(/<td class="sticky left-0 whitespace-nowrap bg-surface-1 /);
    expect(html).toContain('<td class="break-words ');
    expect(html.match(/data-testid="why-chips"/g)).toHaveLength(4);                       // one under the result (phone), one in its column
    expect(html).toContain('class="mt-1 block sm:hidden"');
    expect(html).toContain('<th scope="col" class="hidden w-24 px-4 text-left font-medium sm:table-cell">Stages</th>');
  });

  it("D6 + D7: the evidence reads in plain words, the result first; the code waits under Advanced", () => {
    const pack = evidenceSample as unknown as EvidencePack;
    const html = renderToStaticMarkup(<EvidenceView pack={pack} hiddenBlocks={new Set()} showFailed={false} />);
    const strip = html.slice(html.lastIndexOf("<table", html.indexOf('data-testid="verify-strip"')), html.indexOf("</table>"));
    expect(strip.indexOf(">Result</th>")).toBeLessThan(strip.indexOf(">Condition</th>"));
    expect(strip).toContain("table-fixed");
    expect(strip).toContain("RSI(14) 63.6 above 50");
    expect(strip).toContain("close 103.80 above SMA(close, 10) 101.33");
    expect(strip).not.toContain("font-mono");
    expect(strip).not.toContain("&gt;");
    // no raw ScreenQL in the default view's words (the chart's own legend keeps the engine's labels)
    const words = html.slice(html.indexOf('aria-label="Why it matched"'), html.indexOf("data-details="));
    expect(words).toContain("RSI(14) above 50");
    expect(words).not.toContain("RSI(14) &gt; 50");
    expect(words).not.toContain("font-mono text-fg");
    const code = html.slice(html.indexOf("data-details="));
    expect(code).toContain("Advanced: the code");
    expect(code).toContain(pack.screen.text.replace(/"/g, "&quot;").replace(/>/g, "&gt;"));
  });

  it("D6: plain words keep the engine's names and numbers", () => {
    expect(plainCondition("RSI(14) >= 60 AND close != open")).toBe("RSI(14) at or above 60 and close is not open");
    expect(plainCondition('NearZone("demand", 20)')).toBe("NearZone(demand, 20)");
    expect(plainCondition("CrossAbove(EMA(close, 20), SMA(close, 50))")).toBe("EMA(close, 20) crosses above SMA(close, 50)");
    expect(plainCondition("NOT (RSI(14) < 30 OR volume <= 3 * SMA(volume, 20))")).toBe("not (RSI(14) below 30 or volume at or below 3 * SMA(volume, 20))");
  });

  it("D7: a stage's ScreenQL sits under its own Advanced disclosure; the formula block is labelled in words", () => {
    const row = read("src/screener/StageRow.tsx");
    expect(row).toMatch(/<details className="min-w-0 basis-full" data-testid="stage-code">\s*<summary[^>]*>Advanced: the code<\/summary>/);
    expect(row).not.toContain('<span className="text-t12 text-fg-muted">ScreenQL</span>');
  });

  it("D8: the six pickers are the shared combobox; the timeframe list keeps all nine", () => {
    for (const f of ["src/pages/ScreenerPage.tsx", "src/screener/TimeframeChip.tsx", "src/screener/ParamInline.tsx", "src/screener/WatchlistPicker.tsx",
      "src/screener/LibraryTools.tsx", "src/screener/EtfPanel.tsx"]) {
      expect(read(f), f).not.toMatch(/<select\b/);
      expect(read(f), f).toMatch(/<Combobox\b/);
    }
    expect(TIMEFRAMES).toEqual(["1m", "3m", "5m", "15m", "30m", "1h", "1d", "1w", "1M"]);
    const html = canvas([newStage(reg)]);
    expect(html).toContain('role="combobox"');
    expect(html).not.toContain("<select");
  });

  it("D9: on a phone the header's tools fold into one More sheet", () => {
    const page = read("src/pages/ScreenerPage.tsx");
    expect(page).toContain('const NARROW_QUERY = "(max-width: 639px)";');
    expect(page).toContain('data-testid="screener-more"');
    expect(page).toContain('<Sheet side="bottom" open={moreOpen} onOpenChange={setMoreOpen} title="More">');
  });

  it("D10: the built-in NIFTY 50 list carries its date and says so when it is used", () => {
    expect(NIFTY50_FALLBACK_AS_OF).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    const asOf = new Date(`${NIFTY50_FALLBACK_AS_OF}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
    expect(NIFTY50_FALLBACK_NOTE).toContain(`as of ${asOf}`);
    expect(read("src/pages/ScreenerPage.tsx")).toContain('{universe.builtin && <p className="mt-1 text-t12 text-warn" data-testid="universe-builtin">{NIFTY50_FALLBACK_NOTE}</p>}');
  });
});
