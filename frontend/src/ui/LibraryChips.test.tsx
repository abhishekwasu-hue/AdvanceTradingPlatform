import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { LibraryChips } from "./LibraryChips";
import { SUGGESTED_NOTE } from "./strategyLabels";

/** STRAT-X (owner decision 30 Oct, D2): the Library card shows a built-in's stop and targets apart, as suggested values. */
describe("STRAT-X: the Library card's suggested stop and targets", () => {
  it("puts the stop and the targets under the suggested note, the other settings as before, and never a minimum R:R", () => {
    const html = renderToStaticMarkup(<LibraryChips params={{ ema_fast: 9, atr_mult_sl: 1, target_rr: [1.5, 2], min_rr: 1.2 }} />);
    const [settings, suggested] = html.split("data-suggested-exits");
    expect(settings).toContain("Fast EMA 9");
    expect(settings).not.toContain("Stop (x ATR)");
    expect(suggested).toContain(SUGGESTED_NOTE);
    expect(suggested).toContain("Stop (x ATR) 1");
    expect(suggested).toContain("Targets (R) 1.5 / 2");
    expect(html).not.toContain("R:R");
  });

  it("shows no suggested block for a strategy without a stop or targets", () => {
    const html = renderToStaticMarkup(<LibraryChips params={{ ema_fast: 9 }} />);
    expect(html).toContain("Fast EMA 9");
    expect(html).not.toContain(SUGGESTED_NOTE);
    expect(renderToStaticMarkup(<LibraryChips params={null} />)).toBe("");
  });
});
