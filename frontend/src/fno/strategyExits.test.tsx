import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { BasketPreview, BasketResult } from "./api";
import { ConfirmView, ResultView } from "./BasketPanelV2";
import { ExitSentenceV2, StrategyExitsV2 } from "./StrategyExitsV2";
import panelSource from "./BasketPanelV2.tsx?raw";
import sectionSource from "./StrategyExitsV2.tsx?raw";
import sheetSource from "./CombinedExitV2.tsx?raw";
import {
  defaultsOfForm, emptyForm, exitErrors, exitSentence, exitWords, exitsAsk, hasExits, isSpread, prefillForm, readExitDefaults, trailFields,
  type ExitLeg, type StrategyExitForm,
} from "./strategyExits";
import { chipText, trailErrors, trailOf, trailWords, type CombinedExit, type CombinedForm } from "./combinedExit";

/** SX1 / SX2 / SX5 / SX6: "Exits for this strategy" on the basket ticket, its Review sentence and the result line. */

const noop = () => undefined;
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const SPREAD: ExitLeg[] = [{ side: "SELL", underlying: "NIFTY" }, { side: "BUY", underlying: "NIFTY" }];
const form = (over: Partial<StrategyExitForm> = {}): StrategyExitForm => ({ ...emptyForm(SPREAD), ...over });

describe("SX1: the section starts empty or from the user's own defaults", () => {
  it("defaults to the spot level for a spread and the ₹ profit / loss otherwise; no number is invented", () => {
    expect(isSpread(SPREAD)).toBe(true);
    expect(emptyForm(SPREAD)).toMatchObject({ measure: "SPOT", sl: "", target: "", trailKind: "NONE", fromDefaults: false });
    expect(emptyForm([{ side: "BUY", underlying: "NIFTY" }]).measure).toBe("MTM_INR");
    expect(prefillForm(null, SPREAD)).toEqual(emptyForm(SPREAD));
    expect(hasExits(emptyForm(SPREAD))).toBe(false);
    expect(exitsAsk(emptyForm(SPREAD))).toBeNull();                       // empty: no strategy exit at all
    expect(exitErrors(emptyForm(SPREAD))).toEqual([]);
  });

  it("pre-fills only from saved defaults, marked, and reads a bad stored value as not set", () => {
    expect(readExitDefaults({})).toBeNull();
    expect(readExitDefaults({ measure: "LIVE", sl_value: -5 })).toBeNull();
    const d = readExitDefaults({ measure: "PREMIUM_PCT", sl_value: 100, target_value: 50, trail_kind: "BREAK_EVEN", trail_after: 25 });
    const f = prefillForm(d, SPREAD);
    expect(f).toMatchObject({ measure: "PREMIUM_PCT", sl: "100", target: "50", trailKind: "BREAK_EVEN", after: "25", fromDefaults: true });
    expect(defaultsOfForm(f)).toEqual({ measure: "PREMIUM_PCT", sl_value: 100, target_value: 50, trail_kind: "BREAK_EVEN", trail_after: 25,
      trail_lock: null, trail_step: null, trail_by: null });
    const html = renderToStaticMarkup(<StrategyExitsV2 form={f} onChange={noop} legCount={2} />);
    expect(text(html)).toContain("from your defaults");
    expect(text(renderToStaticMarkup(<StrategyExitsV2 form={form()} onChange={noop} legCount={2} />))).not.toContain("from your defaults");
  });

  it("renders before Review with the three measures, empty boxes and the trailing choices", () => {
    const html = text(renderToStaticMarkup(<StrategyExitsV2 form={form()} onChange={noop} legCount={2} />));
    for (const w of ["Exits for this strategy", "Spot level", "Combined ₹ profit / loss", "% of net premium", "Overall stop-loss (spot level)",
      "Overall target (spot level)", "Lock and trail", "Trail to break-even", "Optional."]) expect(html).toContain(w);
    expect(renderToStaticMarkup(<StrategyExitsV2 form={form()} onChange={noop} legCount={2} />)).not.toMatch(/value="\d/);
    expect(panelSource.indexOf("{legs.length > 0 && exits}")).toBeLessThan(panelSource.indexOf('data-basket="execute"'));
    for (const banned of [/\bbuy\b/i, /\bsell\b/i, /text-\[/, /#[0-9a-f]{6}/i]) expect(sectionSource).not.toMatch(banned);
  });
});

describe("SX2: the trailing kinds and their checks", () => {
  it("asks for the right numbers per kind, in the measure's unit", () => {
    expect(trailFields("LOCK_TRAIL", "MTM_INR").map((f) => f.key)).toEqual(["after", "lock", "step", "by"]);
    expect(trailFields("BREAK_EVEN", "PREMIUM_PCT")).toEqual([{ key: "after", label: "Once the gain reaches (%)" }]);
    expect(trailFields("NONE", "SPOT")).toEqual([]);
    expect(exitErrors(form({ measure: "MTM_INR", trailKind: "TRAIL", step: "100", by: "50" })))
      .toContain("A trailing stop needs a stop-loss to start from.");
    expect(exitErrors(form({ measure: "MTM_INR", trailKind: "LOCK_TRAIL", after: "100", lock: "20", step: "50", by: "60" })))
      .toEqual(["The stop cannot shift further than the move itself."]);
    expect(exitErrors(form({ measure: "MTM_INR", trailKind: "BREAK_EVEN", after: "1500" }))).toEqual([]);
    expect(exitErrors(form({ trailKind: "BREAK_EVEN", after: "50" })))
      .toContain("A spot lock needs a stop-loss or a target too, so the exit knows which way is yours.");
  });

  it("sends only the user's numbers for the chosen kind", () => {
    expect(exitsAsk(form({ measure: "MTM_INR", sl: "5,000", target: "3000", trailKind: "LOCK_TRAIL", after: "2000", lock: "500", step: "1000", by: "500" })))
      .toEqual({ measure: "MTM_INR", sl_value: 5000, target_value: 3000, trailing: { kind: "LOCK_TRAIL", after: 2000, lock: 500, step: 1000, by: 500 } });
    expect(exitsAsk(form({ sl: "24300" }))).toEqual({ measure: "SPOT", sl_value: 24300, target_value: null });
  });

  it("the edit sheet on Positions takes the new kinds too (SX6)", () => {
    const cx: CombinedForm = { ticked: [1, 2], measure: "MTM_INR", sl: "", target: "", check: "TOUCH", timeframe: 15, trailKind: "LOCK_TRAIL",
      trailA: "2000", trailB: "500", trailC: "1000", trailD: "500" };
    expect(trailErrors(cx)).toEqual([]);
    expect(trailOf(cx)).toEqual({ kind: "LOCK_TRAIL", after: 2000, lock: 500, step: 1000, by: 500 });
    expect(trailWords(cx)).toContain("then for every further ₹1,000, it moves up ₹500.");
    expect(trailOf({ ...cx, trailKind: "BREAK_EVEN", trailA: "1500" })).toEqual({ kind: "BREAK_EVEN", after: 1500 });
    const chip = chipText({ id: 1, origin: "SX", measure: "MTM_INR", sl_value: 1000, target_value: null, spot_symbol: null, check_mode: "TOUCH",
      timeframe_minutes: null, status: "ARMED", last_value: 0, distance: { sl: 1000, target: null }, version: 1, legs: [], words: "", stale: [] } as CombinedExit);
    expect(chip.title).toBe("Strategy exit · 0 legs");
  });
});

describe("SX5: the Review screen and the result say it in plain words", () => {
  it("words the exits as the owner wrote them", () => {
    expect(exitWords(form({ measure: "MTM_INR", sl: "5000", target: "3000" }), 2, "NIFTY"))
      .toBe("Exit all 2 legs if loss reaches −₹5,000 or profit reaches +₹3,000. Sold legs exit first, then the hedges.");
    expect(exitWords(form({ measure: "PREMIUM_PCT", sl: "100", trailKind: "BREAK_EVEN", after: "25" }), 4, "NIFTY"))
      .toBe("Exit all 4 legs if loss reaches −100% of the net premium; trailing: once the gain reaches 25%, the stop moves to break-even. "
        + "The stop only moves your way. Sold legs exit first, then the hedges.");
    expect(exitWords(form({ sl: "24300", target: "23700" }), 2, "NIFTY")).toContain("NIFTY spot reaches 24,300.00 (stop-loss) or NIFTY spot reaches 23,700.00 (target)");
    expect(exitWords(form(), 2, "NIFTY")).toBe("No strategy exit: each leg keeps its own stop.");
  });

  it("F3: the review sentence shows the ₹ / % figures as the shared signed value (− ▼ a loss, + ▲ a profit)", () => {
    const html = renderToStaticMarkup(<p><ExitSentenceV2 parts={exitSentence(form({ measure: "MTM_INR", sl: "5000", target: "3000" }), 2, "NIFTY")} /></p>);
    expect(text(html)).toContain("Exit all 2 legs if loss reaches ▼ −₹5,000 or profit reaches ▲ +₹3,000 . Sold legs exit first");
    expect(html).toContain('data-signed="down"');
    expect(html).toContain('data-signed="up"');
    expect(renderToStaticMarkup(<p><ExitSentenceV2 parts={exitSentence(form({ sl: "24300" }), 2, "NIFTY")} /></p>)).not.toContain("data-signed");
  });

  it("shows the sentence on Review and the outcome on the result", () => {
    const p = { legs: [], totals: null, totals_errors: [], refusals: [], can_execute: true, confirm_label: "Place 2 legs" } as unknown as BasketPreview;
    const review = text(renderToStaticMarkup(<ConfirmView preview={p} busy={false} onBack={noop} onPlace={noop} exitWords="Exit all 2 legs if loss reaches ₹5,000." />));
    expect(review).toContain("Exits for this strategy Exit all 2 legs if loss reaches ₹5,000.");
    expect(text(renderToStaticMarkup(<ConfirmView preview={p} busy={false} onBack={noop} onPlace={noop} />))).not.toContain("Exits for this strategy");
    const base: BasketResult = { mode: "PAPER", status: "PLACED", reasons: [], legs: [], placed: 2, next_steps: [] };
    const armed = text(renderToStaticMarkup(<ResultView result={{ ...base, exits: { status: "ARMED", id: 7, words: "If combined loss reaches ₹5,000, these 2 legs exit.", legs: 2 } }}
      onRemaining={noop} onEdit={noop} onClear={noop} onClose={noop} />));
    expect(armed).toContain("Strategy exit set on the filled legs: If combined loss reaches ₹5,000, these 2 legs exit. Change or remove it on Positions.");
    const missed = text(renderToStaticMarkup(<ResultView result={{ ...base, exits: { status: "NOT_SET", reason: "No live price for the group right now" } }}
      onRemaining={noop} onEdit={noop} onClear={noop} onClose={noop} />));
    expect(missed).toContain("Strategy exit not set: No live price for the group right now. The legs keep their own stops.");
    const money = renderToStaticMarkup(<ResultView result={{ ...base, exits: { status: "ARMED", id: 7, words: "If combined loss reaches ₹5,000, these 2 legs exit.", legs: 2,
      measure: "MTM_INR", stop_now: -5000, target_value: 3000 } }} onRemaining={noop} onEdit={noop} onClear={noop} onClose={noop} />);
    expect(text(money)).toContain("Strategy exit set on the filled legs: stop at ▼ −₹5,000 , target ▲ +₹3,000 . Change or remove it on Positions.");
    expect(text(renderToStaticMarkup(<ResultView result={base} onRemaining={noop} onEdit={noop} onClear={noop} onClose={noop} />))).not.toContain("Strategy exit");
  });
});

describe("F4 / F7: one UI per concept, one short line and the rest in Details", () => {
  it("the trailing choice is the same option cards as the exit sheet - no browser select", () => {
    const html = renderToStaticMarkup(<StrategyExitsV2 form={form({ measure: "MTM_INR", trailKind: "LOCK_TRAIL" })} onChange={noop} legCount={2} />);
    expect(html).not.toContain("<select");
    expect(sectionSource).not.toContain("<select");
    expect(html).toContain('data-option-cards="strategy-exit-trail"');
    expect(html).toContain('data-option-cards="strategy-exit-measure"');
    for (const k of ["NONE", "LOCK", "TRAIL", "LOCK_TRAIL", "BREAK_EVEN"]) expect(html).toContain(`data-exits-trail-kind="${k}"`);
    expect(html).toMatch(/data-exits-trail-kind="LOCK_TRAIL"[^>]*data-checked="true"/);
    expect(sheetSource).toContain("<OptionCards name=\"combined-trailing\"");
  });

  it("the section's body is one short line; the longer explanations sit once in Details", () => {
    const html = renderToStaticMarkup(<StrategyExitsV2 form={form({ trailKind: "LOCK" })} onChange={noop} legCount={2} />);
    expect(html.match(/data-details=/g)).toHaveLength(1);
    const outside = text(html.replace(/<details[\s\S]*<\/details>/, ""));
    expect(outside).toContain("Optional. All legs exit together, sold legs first.");
    expect(outside).not.toContain("never back");
    expect(outside).not.toContain("Leave it empty");
    const inside = text(html.match(/<details[\s\S]*<\/details>/)?.[0] ?? "");
    expect(inside).toContain("The stop only moves your way, never back.");
    expect(inside).toContain("When the legs fill, all 2 legs exit together");
  });
});
