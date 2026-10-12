import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { LadderCriterion } from "../types";
import LadderEvidence, { DisciplineWeeks } from "./LadderEvidence";
import { LOSING, OK, TOO_FEW } from "./samples";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");

describe("LadderEvidence (N2-1)", () => {
  it("shows the counts and waits for enough trades before any figure", () => {
    const words = text(renderToStaticMarkup(<LadderEvidence evidence={TOO_FEW} />));
    expect(words).toContain("Your figures");
    expect(words).toContain("2 trades · 1 won · 1 lost · 0 flat");
    expect(words).toContain("2 of 20 closed trades - the figures appear at 20.");
    expect(words).not.toContain("Kelly");
    expect(words).not.toContain("Risk of ruin");
  });

  it("shows the figures, the Kelly reference with its caveat and the ruin bound", () => {
    const html = renderToStaticMarkup(<LadderEvidence evidence={OK} />);
    const words = text(html);
    expect(words).toContain("Win rate 60.0%");
    expect(words).toContain("Expectancy per trade +₹80.00");
    expect(words).toContain("Profit factor 3.00");
    expect(words).toContain("Average win / average loss 2.00");
    expect(words).toContain("Kelly reference: 40.0% of capital lost on a losing trade");
    expect(words).toContain("not a position size. Sizing stays with your risk cap.");
    expect(words).toContain("Risk of ruin (upper bound): At most 0.04% chance of ever falling 10% (₹1,000.00) below your starting capital.");
    expect(words).toContain("Information only: nothing here sizes or places a trade.");
    expect(html).toContain('aria-label="Your figures"');
    expect(html).not.toMatch(/<button|<input/);                 // information only: no control
    expect(html).toContain("text-up");                          // positive expectancy, the up colour
  });

  it("states a losing record plainly", () => {
    const words = text(renderToStaticMarkup(<LadderEvidence evidence={LOSING} />));
    expect(words).toContain("Expectancy per trade -₹125.00");
    expect(words).toContain("Below zero (-125.0%): these past outcomes lost money on average.");
    expect(words).toContain("Certain in this model: with these outcomes a fall of 50% (₹5,000.00) below the start comes sooner or later.");
  });
});

describe("DisciplineWeeks (N2-1)", () => {
  const base: LadderCriterion = { key: "discipline_score", label: "Discipline score (mean of recent weekly scores)", value: 72.5,
                                  target: 70, comparison: "at_least", met: false, weeks_needed: 4, status: "too_few_weeks",
                                  weeks: [{ week: "2026-W40", score: 70 }, { week: "2026-W41", score: 75 }] };

  it("lists the scored weeks oldest first with how many are missing", () => {
    const html = renderToStaticMarkup(<DisciplineWeeks criterion={base} />);
    const words = text(html);
    expect(words.indexOf("W40 · 70")).toBeLessThan(words.indexOf("W41 · 75"));
    expect(words).toContain("2 of 4 weeks scored");
    expect(html).toContain('title="2026-W41: 75"');
  });

  it("says why it is not measured, and renders nothing with nothing to say", () => {
    expect(text(renderToStaticMarkup(<DisciplineWeeks criterion={{ ...base, weeks: [], status: "engine_off", value: null, met: null }} />)))
      .toContain("discipline engine is off");
    expect(renderToStaticMarkup(<DisciplineWeeks criterion={{ ...base, weeks_needed: 2, status: "ok" }} />)).toContain("W41 · 75");
    expect(renderToStaticMarkup(<DisciplineWeeks criterion={{ ...base, weeks: [], weeks_needed: undefined, status: "ok" }} />)).toBe("");
  });
});
