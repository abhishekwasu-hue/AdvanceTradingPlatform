import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { BoardPosition, PositionsBoard } from "../types";
import { PositionsBoardView } from "./PositionsBoardCard";
import { driftLine, duration, exitLabel, money, stopLine, targetLine } from "./board";
import { tProgression as t } from "./i18n";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&lt;/g, "<").replace(/&amp;/g, "&").replace(/\s+/g, " ");

// Shaped as tests/test_n2_positions_board.py's rows.
const P: BoardPosition = {
  trade_id: 11, symbol: "INFY", mode: "PAPER", direction: "LONG", quantity: 10, instrument_kind: "UNDERLYING", underlying: null, leg_group_id: null,
  entry_price: 100, entry_time: "2026-10-12T04:25:00+00:00", time_in_trade_minutes: 95, mark_price: 104, mark_time: "2026-10-12T05:58:00+00:00",
  mark_age_minutes: 2, price_basis: "mark", pnl: 40, r_multiple: 1, initial_risk: 40, risk_basis: "plan stop", stop: 94, stop_status: "ok",
  to_stop_r: 2.5, target: 110, target_status: "ok", to_target_r: 1.5,
  plan: { plan_id: 7, setup: "PULLBACK_AT_LEVEL", entry: 100, stop_loss: 96, target: 110, reward_risk: 2.5 },
  original_stop: 96, stop_drift: "widened", exit: "one_click",
};
const BOARD: PositionsBoard = {
  positions: [P, { ...P, trade_id: 12, symbol: "TCS", mode: "LIVE", exit: "broker_app", stop_drift: "unchanged", pnl: -20, r_multiple: -0.5, plan: null }],
  totals: { positions: 2, pnl: 20, r: 0.5, initial_risk: 80, to_stops_from_marks: 200, without_stop: 0, widened_stops: 1, near_stop: 0, unmarked: 0 },
  lockout: { reason: "Daily loss limit reached", until: "2026-10-12T18:30:00+00:00" },
  exits: "Exits are never locked: stops, targets, time exits and Exit now always work.", near_r: 0.25, as_of: "2026-10-12T06:00:00+00:00",
  note: "P&L at the position monitor's last marks.",
};

describe("board helpers (N2-6)", () => {
  it("formats money, durations and the status lines", () => {
    expect(money(40)).toBe("+₹40.00");
    expect(money(-1234.5)).toBe("-₹1,234.50");
    expect(duration(12)).toBe("12 min");
    expect(duration(95)).toBe("1 h 35 min");
    expect(duration(3000)).toBe("2 d 2 h");
    expect(stopLine(P, t)).toBe("stop 94 · 2.50 R away");
    expect(stopLine({ ...P, stop_status: "through" }, t)).toBe("stop 94 · through (the monitor exits it)");
    expect(stopLine({ ...P, stop: null, stop_status: null }, t)).toBe("no stop");
    expect(targetLine({ ...P, target_status: "reached" }, t)).toBe("target 110 · reached");
    expect(driftLine(P, t)).toBe("Stop widened from 96 to 94 against the plan stop");
    expect(driftLine({ ...P, stop_drift: "unchanged" }, t)).toBeNull();
    expect(exitLabel(P, t)).toBe("Exit PAPER INFY ×10 now");
    expect(exitLabel({ ...P, exit: "automatic" }, t)).toBe("Exits run automatically (stop, target, time)");
  });
});

describe("PositionsBoardView (N2-6)", () => {
  it("shows the lockout as a fact, highlights a widened stop and keeps Exit now one enabled click", () => {
    const html = renderToStaticMarkup(<PositionsBoardView board={BOARD} exiting={null} message={null} onExit={() => undefined} />);
    const words = text(html);
    expect(words).toContain("New entries are locked: Daily loss limit reached. Exits still work.");
    expect(words).toContain("2 open · P&L +₹20.00 (0.50 R) · ₹200.00 back to the stops from the marks");
    expect(words).toContain("+₹40.00 · 1.00 R");
    expect(words).toContain("1 h 35 min in trade · mark 2 min old");
    expect(words).toContain("Plan: PULLBACK_AT_LEVEL · entry 100 · stop 96 · target 110");
    expect(html).toContain('data-drift="widened"');
    expect(html).toMatch(/<button type="button" class="[^"]*">Exit PAPER INFY ×10 now<\/button>/);   // enabled, despite the lockout
    expect(words).toContain("LIVE: close it in your broker app");
    expect(html.match(/<button/g)?.length).toBe(1);
  });
});
