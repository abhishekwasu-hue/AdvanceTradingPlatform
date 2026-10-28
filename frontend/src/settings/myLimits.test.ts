import { describe, expect, it } from "vitest";
import { blockedBy } from "../fno/ticketModel";
import { LIMIT_FIELDS, NOT_SET, configOf, countSet, readSwitches, textOf } from "./myLimits";

// DEF-0 (owner, 28 Oct): every limit blank = not set, nothing pre-filled; the ticket names what holds it back.
describe("DEF-0 My limits", () => {
  it("a saved config with nothing set shows every limit blank - no platform number", () => {
    const blank = textOf({ capital: null, risk_per_trade_pct: null });
    expect(Object.values(blank).every((v) => v === "")).toBe(true);
    expect(countSet({ capital: null })).toBe(0);
    expect(NOT_SET).toBe("Not set (no limit)");
  });

  it("blank fields save as null (not set); numbers must be above 0 and whole where counted", () => {
    const text = { ...textOf(null), risk_per_trade_pct: "0.5", max_trades_per_day: "4" };
    const { config, problem } = configOf(text, null);
    expect(problem).toBeNull();
    expect(config?.risk_per_trade_pct).toBe(0.5);
    expect(config?.max_trades_per_day).toBe(4);
    expect(config?.capital).toBeNull();
    expect(configOf({ ...text, max_trades_per_day: "2.5" }, null).problem).toContain("whole number");
    expect(configOf({ ...text, capital: "-5" }, null).problem).toContain("above 0");
  });

  it("lists every limit of the order once, and the two switches read only an explicit true", () => {
    expect(LIMIT_FIELDS.map((f) => f.key)).toEqual(["capital", "risk_per_trade_pct", "max_daily_loss_pct", "max_trades_per_day",
      "max_open_positions", "max_consecutive_losses", "max_portfolio_risk_pct", "stop_cooldown_minutes", "dd_level_1_pct",
      "dd_level_2_pct", "event_size_cut_pct"]);
    expect(readSwitches(null)).toEqual({ limit_by_experience: false, stop_required_on_sold: false });
    expect(readSwitches({ limit_by_experience: true, stop_required_on_sold: null })).toEqual({ limit_by_experience: true, stop_required_on_sold: false });
  });

  it("D6: a placeable order names nothing; a held one names its rule, and his own limit links to My limits", () => {
    expect(blockedBy({ can_place: true, errors: [], refusals: [] })).toBeNull();
    expect(blockedBy({ can_place: false, errors: [], refusals: ["Your risk per trade (₹500) allows 0 lot(s) at this stop-loss."] }))
      .toEqual({ text: "Your risk per trade (₹500) allows 0 lot(s) at this stop-loss.", link: "/risk-management" });
    expect(blockedBy({ can_place: false, errors: [], refusals: ["NIFTY is in the F&O ban list today."] })?.link).toBeNull();
  });
});
