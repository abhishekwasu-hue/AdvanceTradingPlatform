/** SCR-FIX PR B (frontend): B1 unread counted apart with one action, B4 one count source, B5 operator / negation
 * carried into the verify rows, B6 no replay controls when replay is not available. */
import type { ReactElement } from "react";
import { renderToStaticMarkup as renderBare } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { ApiError } from "../api/errors";
import type { RunResult } from "./api";
import { EvidenceView, type ReplayControls } from "./EvidencePanel";
import { canReplay, verifyRows, type EvidencePack, type EvidenceRow } from "./evidence";
import sample from "./evidenceSample.json";
import { MatchedFoot } from "./FunnelCanvas";
import { countLine, nothingRead, tally, unreadReason, unreadRows } from "./readCounts";
import { UnreadNote } from "./UnreadNote";
import { mergeRuns } from "./universe";
/** M75 NAV-3: the evidence's "Open in Charts" is an in-place link, always inside the app's router. */
const renderToStaticMarkup = (el: ReactElement) => renderBare(<MemoryRouter>{el}</MemoryRouter>);

const pack = sample as unknown as EvidencePack;
const login = "Your broker login has ended. Log in again to read the bars.";
const funnel = (universe: number, withData: number) => ({ universe, with_data: withData, stages: [] });

describe("B1 / B4: one count source, unread never 'did not match'", () => {
  it("counts from the funnel: matched, could not be read, did not match", () => {
    const t = tally(funnel(50, 48), 4);
    expect(t).toEqual({ total: 50, withData: 48, unread: 2, matched: 4, notMatched: 44 });
    expect(countLine(t)).toBe("4 matched · 2 could not be read");
    expect(countLine(tally(funnel(50, 50), 4))).toBe("4 matched");
    // the owner's day: every symbol unread - 0 matched of 50, nothing "did not match"
    expect(tally(funnel(50, 0), 0)).toMatchObject({ unread: 50, notMatched: 0 });
  });

  it("groups the unread rows into one reason with one action (a tie: the action that fixes most)", () => {
    const rows: RunResult[] = [
      { symbol: "A", matched: true, reason: null },
      { symbol: "B", matched: false, reason: null },
      { symbol: "C", matched: false, reason: login, code: "login_ended", action: "login" },
      { symbol: "D", matched: false, reason: "The broker could not be reached.", code: "unreachable", action: "retry" },
    ];
    expect(unreadRows(rows).map((r) => r.symbol)).toEqual(["C", "D"]);
    expect(unreadReason({ results: rows })).toEqual({ code: "login_ended", text: login, action: "login", count: 1 });
    expect(unreadReason({ results: rows.slice(0, 2) })).toBeNull();
    // a preview has no rows: the server's own summary
    expect(unreadReason({ unread_reason: { code: "x", text: "t", action: "retry", count: 3 } })?.count).toBe(3);
  });

  it("a long list's batches merged still count the unread from every batch", () => {
    const batch = (symbols: string[], unread: string[]) => ({
      run_id: 1, text: "t", base_tf: "1d", data_source: "broker:x", scanned: symbols.length, matched: [], disclaimer: "",
      funnel: funnel(symbols.length, symbols.length - unread.length),
      results: symbols.map((s) => (unread.includes(s) ? { symbol: s, matched: false, reason: login, code: "login_ended", action: "login" as const }
        : { symbol: s, matched: false, reason: null })),
    });
    const merged = mergeRuns([batch(["A", "B"], ["A"]), batch(["C", "D"], ["C", "D"])]);
    expect(tally(merged.funnel, merged.matched.length)).toMatchObject({ total: 4, unread: 3, notMatched: 1 });
    expect(unreadReason(merged)?.count).toBe(3);
  });

  it("shows 'N matched · M could not be read', the reason in one line and ONE action", () => {
    const html = renderToStaticMarkup(<UnreadNote tally={tally(funnel(50, 0), 0)} reason={{ text: login, action: "login", count: 50 }} onAction={() => {}} />);
    expect(html).toContain("0 matched · 50 could not be read");
    expect(html).toContain(`50 stocks: ${login}`);
    expect(html).toContain(">Log in<");
    expect((html.match(/<button/g) ?? []).length).toBe(1);
    expect(html).not.toContain("did not match");
    expect(renderToStaticMarkup(<UnreadNote tally={tally(funnel(5, 5), 1)} reason={null} onAction={() => {}} />)).toBe("");
  });

  it("the Conditions card says 'Matched N of 50 · K had data' from the same funnel", () => {
    const html = renderToStaticMarkup(<MatchedFoot matched={1} of={50} withData={2} kills={false} live={false} />);
    expect(html.replace(/<[^>]+>/g, "")).toContain("Matched 1 of 50 · 2 had data");
    expect(renderToStaticMarkup(<MatchedFoot matched={1} of={50} withData={50} kills={false} live={false} />)).not.toContain("had data");
  });

  it("reads the run-level 'nothing could be read' error and its action", () => {
    const body = JSON.stringify({ detail: { message: "Could not read any of the 2 stocks. The broker does not send bars for this timeframe.",
      code: "nothing_read", reason_code: "timeframe_refused", action: "timeframe", unread: 2 } });
    expect(nothingRead(new ApiError(422, "x", "x", body))).toEqual({
      text: "Could not read any of the 2 stocks. The broker does not send bars for this timeframe.", action: "timeframe" });
    expect(nothingRead(new ApiError(422, "x", "x", JSON.stringify({ detail: "the screen did not pass validation" })))).toBeNull();
    expect(nothingRead(new Error("x"))).toBeNull();
  });
});

describe("B5: the verify rows say what the screen says", () => {
  const row = (over: Partial<EvidenceRow>): EvidenceRow => ({ id: "b1.c1", block: "b1", text: "x != 1", passed: true, terms: [], verify: "x != 1",
    drawable: null, ...over });
  const withRows = (rows: EvidenceRow[]): EvidencePack => ({ ...pack, rows, drawables: [] });

  it("marks an OR's true branch ✅, the others grey 'not needed', and labels the group", () => {
    const p = withRows([
      row({ id: "b1.c1", text: "close != 1", state: "met", group: { id: "b1.g1", op: "ANY", negated: false } }),
      row({ id: "b1.c2", text: "close != close", passed: null, state: "not_needed", raw: false, group: { id: "b1.g1", op: "ANY", negated: false } }),
    ]);
    expect(verifyRows(p, false).map((r) => r.text)).toEqual(["One of: close != 1", "One of: close != close"]);
    const html = renderToStaticMarkup(<EvidenceView pack={p} hiddenBlocks={new Set()} showFailed={false} />);
    expect(html).toContain("not needed");
    expect((html.match(/✅/g) ?? []).length).toBeGreaterThanOrEqual(1);
  });

  it("a NOT over a group: rows read as negated, and a condition required false is never ✅ for being true", () => {
    const p = withRows([
      row({ id: "b1.c1", text: "NOT (trend != UP)", verify: "NOT (trend != UP)", passed: false, state: "failed", raw: true, required: false,
        group: { id: "b1.g1", op: "ALL", negated: true } }),
      row({ id: "b1.c2", text: "NOT (trend != DOWN)", verify: "NOT (trend != DOWN)", passed: true, state: "met", raw: false, required: false,
        group: { id: "b1.g1", op: "ALL", negated: true } }),
    ]);
    const shown = verifyRows(p, true);
    expect(shown.map((r) => r.text)).toEqual(["NOT (trend != UP)", "NOT (trend != DOWN)"]);
    const html = renderToStaticMarkup(<EvidenceView pack={p} hiddenBlocks={new Set()} showFailed />);
    const upRow = html.slice(html.indexOf('data-row="b1.c1"'), html.indexOf('data-row="b1.c2"'));
    expect(upRow).toContain("❌");
    expect(upRow).not.toContain("✅");
    // "Show failed" off: the failed row is hidden, the met row stays - the same rows as the caption
    expect(verifyRows(p, false).map((r) => r.id)).toEqual(["b1.c2"]);
  });
});

describe("B6: replay controls only when replay is available", () => {
  const controls: ReplayControls = { steps: 5, index: 4, loading: false, onStep: () => {}, onReset: () => {} };
  it("hides the controls when the server says replay is not available, or refused it", () => {
    expect(canReplay({ replay_available: true }, 5, false)).toBe(true);
    expect(canReplay({}, 5, false)).toBe(true);                       // an older server: as before
    expect(canReplay({ replay_available: false }, 5, false)).toBe(false);
    expect(canReplay({ replay_available: true }, 5, true)).toBe(false);
    expect(canReplay({ replay_available: true }, 1, false)).toBe(false);
    const off = renderToStaticMarkup(<EvidenceView pack={{ ...pack, replay_available: false }} hiddenBlocks={new Set()} showFailed={false}
      replay={canReplay({ replay_available: false }, 5, false) ? controls : undefined} />);
    expect(off).not.toContain('data-testid="replay-bar"');
    expect(off).not.toContain("Step back one bar");
  });
});
