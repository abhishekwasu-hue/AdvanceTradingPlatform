import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { StatusBarV2 } from "../terminal/StatusBarV2";
import type { TerminalStatus } from "../terminal/api";
import { trade } from "../trading/trading.fixture";
import panelsSource from "../terminal/PanelsV2.tsx?raw";
import positionsSource from "../trading/PositionsV2.tsx?raw";
import sheetSource from "./CombinedExitV2.tsx?raw";
import { CombinedExitChipV2, CombinedExitFormV2, startForm, type FormView } from "./CombinedExitV2";
import {
  active, amount, bodyOf, chipText, defaultMeasure, distanceText, exitParam, exitShortCopy, formErrors, newRef, previewWords,
  sheetLegs, staleText, statusSummary, type CombinedExit, type CombinedForm, type SheetLeg,
} from "./combinedExit";
import type { ContractFacts } from "./contracts";

/** CX6 + CX-F1 + CX-F2: the combined stop-loss sheet, the chip with the distance and the stale mark, "Exit short now".
 * Made-up records only (no real account, price or id). */

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");

const option = (id: number, symbol: string, direction: "LONG" | "SHORT", extra = {}) =>
  trade({ id, symbol, direction, exchange: "NFO", instrument_kind: "OPTION", underlying_symbol: "NIFTY", ...extra });

const LEGS: SheetLeg[] = [
  { id: 11, symbol: "DEMO 24900 CE", short: true, underlying: "NIFTY", spotSymbol: "NIFTY 50", spotExchange: "NSE" },
  { id: 12, symbol: "DEMO 25000 CE", short: false, underlying: "NIFTY", spotSymbol: "NIFTY 50", spotExchange: "NSE" },
  { id: 13, symbol: "DEMO 24800 PE", short: true, underlying: "NIFTY", spotSymbol: "NIFTY 50", spotExchange: "NSE" },
];

const form = (over: Partial<CombinedForm> = {}): CombinedForm =>
  ({ ticked: [11, 12, 13], measure: "MTM_INR", sl: "2500", target: "", check: "TOUCH", timeframe: 15, ...over });

const exit = (over: Partial<CombinedExit> = {}): CombinedExit => ({
  id: 7, measure: "MTM_INR", sl_value: 2500, target_value: null, spot_symbol: null, check_mode: "TOUCH", timeframe_minutes: null,
  status: "ARMED", last_value: -400, distance: { sl: 2100, target: null }, version: 1,
  legs: [{ trade_id: 11, symbol: "DEMO 24900 CE", role: "SHORT" }, { trade_id: 12, symbol: "DEMO 25000 CE", role: "LONG" }],
  words: "If combined loss reaches ₹2,500, these 2 legs exit - sold legs first.", stale: [], ...over,
});

describe("CX6 the sheet's legs and default", () => {
  it("only open PAPER positions, the spot from the contract's facts, else the underlying on NSE / BSE", () => {
    const facts = new Map<string, ContractFacts>([["NFO:DEMO 24900 CE", {
      symbol: "DEMO 24900 CE", exchange: "NFO", known: true, listed: true, underlying: "NIFTY", right: "CE", strike: 24900, expiry: "2026-10-27",
      lot_size: 75, label: "x", expired: false, days_left: 3, spot_symbol: "NIFTY 50", spot_exchange: "NSE", roll: null, roll_note: null,
    }]]);
    const legs = sheetLegs([
      option(1, "DEMO 24900 CE", "SHORT"), option(2, "DEMO SENSEX CE", "LONG", { exchange: "BFO", underlying_symbol: "SENSEX" }),
      option(3, "DEMO LIVE", "LONG", { mode: "LIVE" }), option(4, "DEMO CLOSED", "LONG", { exit_time: "2026-10-20T05:00:00" }),
    ], facts);
    expect(legs.map((l) => l.id)).toEqual([1, 2, 3]);              // LIVE the same way (owner principle); closed left out
    expect(legs[0]).toMatchObject({ short: true, spotSymbol: "NIFTY 50", spotExchange: "NSE" });
    expect(legs[1]).toMatchObject({ short: false, spotSymbol: "SENSEX", spotExchange: "BSE" });
    expect(sheetLegs([option(5, "X", "LONG", { leg_role: "SHORT" })])[0].short).toBe(true);
    const mixed = [{ ...LEGS[0], mode: "PAPER" }, { ...LEGS[1], mode: "LIVE" }];
    expect(formErrors(form({ ticked: [11, 12] }), mixed)).toContain("PAPER and LIVE positions cannot share one combined exit.");
    expect(startForm(mixed, null).ticked).toEqual([11]);
  });

  it("a spread (several legs, one underlying, a sold leg) defaults to the spot level; anything else to ₹", () => {
    expect(defaultMeasure(LEGS)).toBe("SPOT");
    expect(defaultMeasure(LEGS.slice(0, 1))).toBe("MTM_INR");
    expect(defaultMeasure([LEGS[1], { ...LEGS[1], id: 99 }])).toBe("MTM_INR");
    expect(defaultMeasure([LEGS[0], { ...LEGS[1], spotSymbol: "NIFTY BANK" }])).toBe("MTM_INR");
    expect(startForm(LEGS, null)).toMatchObject({ ticked: [11, 12, 13], measure: "SPOT", sl: "", target: "" });
  });
});

describe("CX6 the form, the body and the preview sentence", () => {
  it("amounts: positive numbers only (₹ and commas allowed)", () => {
    expect(amount("₹2,500")).toBe(2500);
    expect(amount("0")).toBeNull();
    expect(amount("-5")).toBeNull();
    expect(amount("abc")).toBeNull();
    expect(amount(" ")).toBeNull();
  });

  it("plain reasons: no leg, no level, a bad number, a spot exit across underlyings", () => {
    expect(formErrors(form(), LEGS)).toEqual([]);
    expect(formErrors(form({ ticked: [] }), LEGS)).toContain("Tick at least one open position.");
    expect(formErrors(form({ sl: "" }), LEGS)).toContain("Enter a stop-loss, a target, or both.");
    expect(formErrors(form({ sl: "x" }), LEGS)).toContain("The stop-loss must be a number above zero.");
    const mixed = [LEGS[0], { ...LEGS[1], spotSymbol: "NIFTY BANK" }];
    expect(formErrors(form({ measure: "SPOT", ticked: [11, 12] }), mixed).join(" ")).toMatch(/one underlying/);
    expect(formErrors(form({ measure: "MTM_INR", ticked: [11, 12] }), mixed)).toEqual([]);
  });

  it("the body: ticked legs, the user's own levels, the spot and the closed-candle check only for a spot exit", () => {
    expect(bodyOf(form({ target: "4000" }), LEGS, "ref-12345678")).toEqual({
      scope: "SELECTED", trade_ids: [11, 12, 13], measure: "MTM_INR", sl_value: 2500, target_value: 4000, spot_symbol: null, spot_exchange: null,
      check_mode: "TOUCH", timeframe_minutes: null, client_ref: "ref-12345678",
    });
    const spot = bodyOf(form({ measure: "SPOT", sl: "25100", check: "CLOSE", timeframe: 60, ticked: [12, 11] }), LEGS, "r");
    expect(spot).toMatchObject({ trade_ids: [11, 12], spot_symbol: "NIFTY 50", spot_exchange: "NSE", check_mode: "CLOSE", timeframe_minutes: 60 });
    expect(bodyOf(form({ check: "CLOSE" }), LEGS, "r")).toMatchObject({ check_mode: "TOUCH", timeframe_minutes: null });
  });

  it("the CX6 sentence: 'If combined loss reaches ₹X, these 3 legs exit - sold legs first'", () => {
    expect(previewWords(form(), LEGS)).toBe("If combined loss reaches ₹2,500, these 3 legs exit - sold legs first.");
    expect(previewWords(form({ target: "4000" }), LEGS)).toBe("If combined loss reaches ₹2,500 or combined profit reaches ₹4,000, these 3 legs exit - sold legs first.");
    expect(previewWords(form({ ticked: [12] }), LEGS)).toBe("If combined loss reaches ₹2,500, this leg exits.");
    expect(previewWords(form({ measure: "SPOT", sl: "25100", check: "CLOSE" }), LEGS))
      .toBe("If NIFTY 50 spot reaches 25,100.00 (stop-loss) on a closed candle, these 3 legs exit - sold legs first.");
    expect(previewWords(form({ sl: "" }), LEGS)).toBe("");
  });

  it("a reference per opening fits the route's pattern", () => {
    expect(newRef()).toMatch(/^[A-Za-z0-9-]{8,64}$/);
  });
});

describe("CX6 / CX-F1 the chip and the status bar", () => {
  it("distance in rupees for ₹, in points for spot / premium; masked rupees", () => {
    expect(distanceText({ measure: "MTM_INR" }, 2100)).toBe("₹2,100 to go");
    expect(distanceText({ measure: "MTM_INR" }, 2100, true)).toBe("₹ ••• to go");
    expect(distanceText({ measure: "SPOT" }, 42.5)).toBe("42.50 points to go");
    expect(distanceText({ measure: "SPOT" }, 0)).toBe("reached");
    expect(distanceText({ measure: "SPOT" }, null)).toBe("waiting for a price");
  });

  it("chip words: what, how many legs, the distance; closing while it fires", () => {
    expect(chipText(exit())).toEqual({ title: "Combined stop-loss · 2 legs", distance: "stop ₹2,100 to go", closing: false });
    expect(chipText(exit({ target_value: 3000, distance: { sl: 2100, target: 3400 } })).distance).toBe("stop ₹2,100 to go · target ₹3,400 to go");
    expect(chipText(exit({ sl_value: null, target_value: 3000, distance: { sl: null, target: 3400 } })).title).toBe("Combined target · 2 legs");
    expect(chipText(exit({ status: "FIRING" }))).toMatchObject({ distance: "closing the legs now", closing: true });
  });

  it("CX-F1: a leg on its last traded price is marked stale, never hidden", () => {
    expect(staleText(exit())).toBeNull();
    const stale = exit({ stale: [{ trade_id: 12, symbol: "DEMO 25000 CE", price: 41.5, seen_at: null }] });
    expect(staleText(stale)).toBe("No fresh price for DEMO 25000 CE (last traded 41.50). It keeps watching on the last traded price.");
    const html = renderToStaticMarkup(<ul><CombinedExitChipV2 exit={stale} onEdit={vi.fn()} onRemove={vi.fn()} /></ul>);
    expect(html).toContain("data-stale");
    expect(text(html)).toContain("Stale price");
    expect(text(html)).toContain("Combined stop-loss · 2 legs");
    expect(text(html)).toContain("Edit");
    expect(text(html)).toContain("Remove");
  });

  it("Edit / Remove only while armed (one already closing its legs finishes)", () => {
    const html = renderToStaticMarkup(<ul><CombinedExitChipV2 exit={exit({ status: "FIRING" })} onEdit={vi.fn()} onRemove={vi.fn()} /></ul>);
    expect(html).not.toContain("combined-edit");
    expect(html).not.toContain("combined-remove");
    expect(text(html)).toContain("closing the legs now");
  });

  it("only armed / closing exits count; the status bar shows the nearest one with the stale mark", () => {
    expect(active([exit(), exit({ id: 8, status: "DONE" }), exit({ id: 9, status: "CANCELLED" })]).map((x) => x.id)).toEqual([7]);
    expect(statusSummary([])).toBeNull();
    expect(statusSummary([exit()])).toMatchObject({ text: "Combined exit: ₹2,100 to go", stale: false });
    expect(statusSummary([exit(), exit({ id: 8 })])?.text).toBe("2 combined exits: ₹2,100 to go");
    expect(statusSummary([exit({ status: "FIRING" })])?.text).toBe("Combined exit: closing the legs");
    const status = {
      today: { mtm: 0, booked: 0, note: "" }, open: { count: 2, priced: 2, mtm: 0, as_of: null },
      funds: { balance: 1, available: 1, source: "practice", note: "" }, margin: { used: 0, estimated: true, not_estimated: 0, note: "" },
      mode: { mode: "PAPER", live_allowed: false, message: "" }, feed: { state: "live", message: "" },
      kill_switch: { engaged: false, scope: null, message: "" }, as_of: "2026-10-24T10:00:00Z",
    } as unknown as TerminalStatus;
    const stale = exit({ stale: [{ trade_id: 12, symbol: "DEMO 25000 CE", price: 41.5, seen_at: null }] });
    for (const compact of [false, true]) {
      const bar = renderToStaticMarkup(<StatusBarV2 status={status} failed={false} masked={false} onMask={vi.fn()} compact={compact} combined={[stale]} />);
      expect(bar).toContain('data-status="combined-exit"');
      expect(text(bar)).toContain("Combined exit: ₹2,100 to go · stale price");
    }
    const masked = renderToStaticMarkup(<StatusBarV2 status={status} failed={false} masked onMask={vi.fn()} combined={[exit()]} />);
    expect(text(masked)).not.toContain("₹2,100");
    const none = renderToStaticMarkup(<StatusBarV2 status={status} failed={false} masked={false} onMask={vi.fn()} />);
    expect(none).not.toContain("combined-exit");
  });
});

describe("CX6 the sheet's body", () => {
  const view = (over: Partial<FormView> = {}): FormView => ({ legs: LEGS, form: form(), editing: false, preview: null, checking: false, ...over });

  it("a tick box per leg (sold / bought), the three measures, the preview sentence and 'nothing is sent now'", () => {
    const html = renderToStaticMarkup(<CombinedExitFormV2 view={view()} onChange={vi.fn()} />);
    const out = text(html);
    expect(html.match(/data-leg="/g)).toHaveLength(3);
    expect(out).toContain("All open positions (3)");
    expect(out).toContain("Sold");
    expect(out).toContain("Bought");
    for (const m of ["Combined ₹ profit / loss", "Spot level", "Net premium"]) expect(out).toContain(m);
    expect(out).toContain("If combined loss reaches ₹2,500, these 3 legs exit - sold legs first.");
    expect(out).toContain("Nothing is sent now.");
    expect(html).not.toContain("data-combined-errors");
  });

  it("the server's sentence and value now when it answers; its reasons when it refuses", () => {
    const ok = text(renderToStaticMarkup(<CombinedExitFormV2 view={view({ preview: { ok: true, reasons: [], words: "If combined loss reaches ₹2,500, these 3 legs exit - sold legs first.", value_now: -400, distance: { sl: 2100, target: null } } })} onChange={vi.fn()} />));
    expect(ok).toContain("Combined profit / loss now: −₹400");
    const bad = renderToStaticMarkup(<CombinedExitFormV2 view={view({ preview: { ok: false, reasons: ["A ticked position is already closed"], words: "", value_now: null, distance: { sl: null, target: null } } })} onChange={vi.fn()} />);
    expect(bad).toContain("data-combined-errors");
    expect(text(bad)).toContain("A ticked position is already closed");
  });

  it("the spot measure asks how to check the level; editing keeps the legs and what it watches fixed", () => {
    const spot = text(renderToStaticMarkup(<CombinedExitFormV2 view={view({ form: form({ measure: "SPOT", check: "CLOSE" }) })} onChange={vi.fn()} />));
    expect(spot).toContain("Only when a candle closes");
    expect(spot).toContain("Candle");
    const editing = renderToStaticMarkup(<CombinedExitFormV2 view={view({ editing: true })} onChange={vi.fn()} />);
    expect(editing.match(/<fieldset[^>]*disabled/g)).toHaveLength(2);
    expect(text(editing)).toContain("remove this exit and set a new one");
    expect(startForm([], exit({ target_value: 3000 }))).toMatchObject({ ticked: [11, 12], measure: "MTM_INR", sl: "2500", target: "3000" });
  });

  it("no advice wording and no suggested level: every number is the user's own", () => {
    const out = text(renderToStaticMarkup(<CombinedExitFormV2 view={view({ form: form({ sl: "", target: "" }) })} onChange={vi.fn()} />));
    expect(out).not.toMatch(/\b(buy|sell|recommended|suggested)\b/i);
    expect(renderToStaticMarkup(<CombinedExitFormV2 view={view({ form: form({ sl: "", target: "" }) })} onChange={vi.fn()} />)).toMatch(/data-testid="combined-sl"[^>]*value=""|value=""[^>]*data-testid="combined-sl"/);
  });
});

describe("CX-F2 Exit short now", () => {
  it("reads the notice's link and asks once in plain words", () => {
    expect(exitParam("?exit=301")).toBe(301);
    expect(exitParam("?exit=abc")).toBeNull();
    expect(exitParam("")).toBeNull();
    expect(exitShortCopy("DEMO 24900 CE")).toEqual({
      title: "Exit short now?", body: "DEMO 24900 CE closes at the current price. A combined exit tried to close it and it is still open.", confirm: "Exit short now",
    });
  });

  it("exits are never disabled (ADR-0004): the confirm of Exit short now has no disabled state", () => {
    const short = sheetSource.slice(sheetSource.indexOf("export function ExitShortSheetV2"));
    expect(short).toContain('data-testid="exit-short-confirm"');
    expect(short).not.toMatch(/disabled=/);
    expect(short).toContain("runExit(");
  });

  it("wired on My trades (the link's page) and on the Terminal positions panel", () => {
    expect(positionsSource).toContain("<CombinedExitBarV2");
    expect(positionsSource).toContain("ExitShortSheetV2");
    expect(positionsSource).toContain("exitParam(location.search)");
    expect(panelsSource).toContain("<CombinedExitBarV2");
  });
});
