import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RiskMetricsV2 } from "../journal/JournalStatsV2";
import { movedWords, type StagnationItem } from "../risk/NotMovingV2";
import { bodyOf, draftOf, type RiskLimitsView } from "./riskLimits";
import { defaultsOf, EMPTY_DEFAULTS, exitsFrom, formOf, formPrefill, readDefaults } from "./tradingDefaults";

/** M99 P7 (flag risk_options): the trader's own risk options on the Risk limits page, the scale-out in his exits, the
 *  "not moving" line and the journal's discipline numbers. Each OFF / absent changes nothing. */
const STEP = {
  id: "loss_step_down", label: "Smaller size after losses in a row", help: "", group: "options" as const, enabled: false, saved: false,
  fields: [
    { key: "step_down_losses", label: "Losses in a row", unit: "losses", min: 1, max: 1e9, whole: true, optional: false, value: null, min_open: false },
    { key: "step_down_size_pct", label: "Size then", unit: "% of your size", min: 0, max: 100, whole: false, optional: false, value: null, min_open: true },
    { key: "step_down_wins", label: "Wins in a row to go back", unit: "wins", min: 1, max: 1e9, whole: true, optional: true, value: null, min_open: false },
  ],
};
const VIEW: RiskLimitsView = {
  checks: [STEP, { id: "daily_loss_lock", label: "Keep the daily loss lock all day", help: "", group: "options", enabled: false, saved: false, fields: [] }],
  capital: null, loss_streak_pause: null, on: 0, exits: "Exits are never checked.", step_down_reset: null,
  step_down_resets: [{ value: "wins", label: "After wins in a row" }, { value: "next_day", label: "At the next trading day" }],
};

describe("M99 P7 risk options", () => {
  it("asks when the full size returns, and its wins for a reset by wins; a valueless lock switches on", () => {
    const draft = draftOf(VIEW);
    expect(draft.reset).toBe("");
    expect(bodyOf(VIEW, draft).body).toEqual({ checks: {}, capital: null, loss_streak_pause: null, opposite_positions: null, step_down_reset: null });
    draft.checks.loss_step_down = { enabled: true, values: { step_down_losses: "2", step_down_size_pct: "50", step_down_wins: "" } };
    expect(bodyOf(VIEW, draft).problem).toBe("Smaller size after losses in a row: pick when the full size returns.");
    draft.reset = "wins";
    expect(bodyOf(VIEW, draft).problem).toBe("Smaller size after losses in a row: enter the wins in a row that bring the full size back.");
    draft.reset = "next_day";
    draft.checks.daily_loss_lock = { enabled: true, values: {} };
    const body = bodyOf(VIEW, draft).body as Record<string, unknown>;
    expect(body.step_down_reset).toBe("next_day");
    expect(body.checks).toEqual({ loss_step_down: { enabled: true, values: { step_down_losses: 2, step_down_size_pct: 50, step_down_wins: null } },
      daily_loss_lock: { enabled: true, values: {} } });
  });

  it("with the flag off (no reset choices sent) the body is exactly as before", () => {
    const off: RiskLimitsView = { checks: [], capital: null, loss_streak_pause: null, on: 0, exits: "" };
    expect(bodyOf(off, draftOf(off)).body).toEqual({ checks: {}, capital: null, loss_streak_pause: null, opposite_positions: null });
  });

  it("scale-out: both or neither, carried into a new bot's exits; absent keeps the defaults as before", () => {
    expect(formOf(EMPTY_DEFAULTS)).not.toHaveProperty("scale_out_at_r");
    const form = { ...formOf(EMPTY_DEFAULTS), scale_out_at_r: "1" };
    expect(defaultsOf(form).problems).toContain("Scale-out: enter both the level in R and the share of the lots, or neither.");
    const { value, problems } = defaultsOf({ ...form, scale_out_pct: "50" });
    expect(problems).toEqual([]);
    expect(value.exit_rules).toMatchObject({ scale_out_at_r: 1, scale_out_pct: 50 });
    expect(exitsFrom(value)).toEqual({ scale_out_at_r: 1, scale_out_pct: 50 });
    expect(formPrefill(value, false)).toMatchObject({ exitScaleR: "1", exitScalePct: "50" });
    expect(readDefaults({ exit_rules: { scale_out_at_r: 1 } }).exit_rules?.scale_out_at_r).toBeUndefined();    // one alone: not a rule
  });

  it("the not-moving line and the discipline tiles say their numbers", () => {
    const item: StagnationItem = { trade_id: 1, symbol: "NIFTY24NOVFUT", read: "NIFTY24NOVFUT", bar_minutes: 5, state: "not_moving", bars: 3, moved_pct: 0.4 };
    expect(movedWords(item)).toBe("Moved 0.40% in the last 3 bars of 5 min");
    const html = renderToStaticMarkup(<RiskMetricsV2 m={{ closed: 8, max_drawdown: 300, longest_win_streak: 1, longest_loss_streak: 3,
      streak_now: { kind: "loss", length: 1 }, breaches: { loss_beyond_plan: 2, no_named_setup: 1, total: 3 }, note: "Breaches are counted only; nothing is blocked or changed by them." }} />);
    expect(html).toContain("Max drawdown");
    expect(html).toContain("now: 1 loss in a row");
    expect(html).toContain("2 loss beyond the stop · 1 no setup");
    expect(html).toContain("counted only");
  });
});
