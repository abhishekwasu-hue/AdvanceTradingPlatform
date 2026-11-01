import { describe, expect, it } from "vitest";
import { blockedBy } from "../fno/ticketModel";
import { ApiError } from "../api/errors";
import { LIMIT_FIELDS, NOT_SET, configOf, countSet, readSwitches, saveFailedText, textOf } from "./myLimits";

// DEF-0 (owner, 28 Oct): every limit blank = not set, nothing pre-filled; the ticket names what holds it back.
describe("DEF-0 My limits", () => {
  it("a saved config with nothing set shows every limit blank - no platform number", () => {
    const blank = textOf({ capital: null, risk_per_trade_pct: null });
    expect(Object.values(blank).every((v) => v === "")).toBe(true);
    expect(countSet({ capital: null })).toBe(0);
    expect(NOT_SET).toBe("Off");
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
      "dd_cut_pct", "dd_level_2_pct", "event_size_cut_pct"]);
    expect(readSwitches(null)).toEqual({ limit_by_experience: false, stop_required_on_sold: false });
    expect(readSwitches({ limit_by_experience: true, stop_required_on_sold: null })).toEqual({ limit_by_experience: true, stop_required_on_sold: false });
  });

  it("D6: a placeable order names nothing; a held one names its rule, and his own limit links to My limits", () => {
    expect(blockedBy({ can_place: true, errors: [], refusals: [] })).toBeNull();
    expect(blockedBy({ can_place: false, errors: [], refusals: ["Your risk per trade (₹500) allows 0 lot(s) at this stop-loss."] }))
      .toEqual({ text: "Your risk per trade (₹500) allows 0 lot(s) at this stop-loss.", link: "/settings/risk-limits" });   // M64
    expect(blockedBy({ can_place: false, errors: [], refusals: ["NIFTY is in the F&O ban list today."] })?.link).toBeNull();
  });
});

// M68 PR-5 review: the older Risk form sends the drawdown step's own cut %, beside the step, and shows a refusal inline.
describe("M68 PR-5 My limits: the drawdown cut", () => {
  it("asks for the cut right after the drawdown step; empty = Not set (null); at most 100", () => {
    const keys = LIMIT_FIELDS.map((f) => f.key);
    expect(keys.indexOf("dd_cut_pct")).toBe(keys.indexOf("dd_level_1_pct") + 1);
    expect(textOf({ dd_level_1_pct: 5, dd_cut_pct: null }).dd_cut_pct).toBe("");
    expect(textOf({ dd_level_1_pct: 5, dd_cut_pct: 40 }).dd_cut_pct).toBe("40");
    const text = { ...textOf(null), dd_level_1_pct: "5", dd_cut_pct: "40" };
    expect(configOf(text, null).config).toMatchObject({ dd_level_1_pct: 5, dd_cut_pct: 40 });
    expect(configOf({ ...text, dd_cut_pct: "" }, { dd_cut_pct: 50 }).config?.dd_cut_pct).toBeNull();
    expect(configOf({ ...text, dd_cut_pct: "120" }, null).problem).toBe("Cut the size by: enter at most 100, or leave it blank.");
  });

  it("no label says the size is halved: it is cut by his own %", () => {
    const step = LIMIT_FIELDS.find((f) => f.key === "dd_level_1_pct");
    for (const f of LIMIT_FIELDS) expect(`${f.label} ${f.help}`).not.toMatch(/halve/i);
    expect(step?.label).toBe("Cut size at drawdown");
    expect(step?.help).toContain("cut by your %");
  });

  it("a refused save shows the server's own reason", () => {
    const reason = "Drawdown steps: enter how much the first step cuts the size.";
    expect(saveFailedText(new ApiError(400, reason, reason))).toBe(reason);
  });
});
