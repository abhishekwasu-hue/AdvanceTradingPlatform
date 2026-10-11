import { describe, expect, it } from "vitest";
import { blankToNull, describeRule, policyErrors, problemsFrom, reasonLabel, ruleErrors, type AlertRule, type AlertRuleInput, type NotificationPolicy } from "./rules";

const RULE: AlertRule = {
  id: 1, name: "TCS above SMA", kind: "instrument", screen_id: null, symbol: "TCS", condition: "close > SMA(close, 20)", base_tf: "5m",
  priority: "normal", cooldown_minutes: 30, mode: "instant", digest_every: "hourly", status: "active", expires_at: null,
};
const POLICY: NotificationPolicy = { timezone: "Asia/Kolkata", quiet_start: null, quiet_end: null, max_per_hour: 30, group_window_seconds: 10, eod_digest_time: "15:45" };
const INPUT: AlertRuleInput = { name: "x", kind: "instrument", symbol: "TCS", condition: "close > 1", base_tf: "5m", priority: "normal", cooldown_minutes: 60, mode: "instant", digest_every: "hourly" };

describe("alert rules helpers", () => {
  it("describes instrument, screen and digest rules", () => {
    expect(describeRule(RULE)).toBe("TCS · close > SMA(close, 20) · 5m · instant · normal · cooldown 30 min");
    expect(describeRule({ ...RULE, kind: "screen", screen_id: 7, mode: "digest", digest_every: "eod", cooldown_minutes: 0 }))
      .toBe("screen #7 · 5m · digest (end of day) · normal");
  });

  it("reads the validator's problems with 1-based columns, and nothing else", () => {
    const body = JSON.stringify({ detail: { message: "the condition did not pass validation", problems: [{ message: "compares price with index", pos: 0 }, { message: "unknown function" }] } });
    expect(problemsFrom(body)).toEqual(["compares price with index (at 1)", "unknown function"]);
    expect(problemsFrom(JSON.stringify({ detail: "Rule not found" }))).toEqual([]);
    expect(problemsFrom("<html>502</html>")).toEqual([]);
  });

  it("checks the policy like the server does", () => {
    expect(policyErrors(POLICY)).toEqual([]);
    expect(policyErrors({ ...POLICY, quiet_start: "22:00" })).toEqual(["Give both quiet-hours times, or neither."]);
    expect(policyErrors({ ...POLICY, quiet_start: "22:00", quiet_end: "7:00" })).toEqual(["Quiet end must be HH:MM (24-hour)."]);
    expect(policyErrors({ ...POLICY, quiet_start: "22:00", quiet_end: "07:00" })).toEqual([]);
    expect(policyErrors({ ...POLICY, max_per_hour: 0, group_window_seconds: 601 })).toHaveLength(2);
  });

  it("checks a new rule", () => {
    expect(ruleErrors(INPUT)).toEqual([]);
    expect(ruleErrors({ ...INPUT, name: " ", condition: "", symbol: "TCS;DROP", base_tf: "2m", cooldown_minutes: -1 })).toHaveLength(5);
  });

  it("labels reason codes and keeps unknown ones", () => {
    expect(reasonLabel("quiet_hours")).toBe("Held for quiet hours");
    expect(reasonLabel("something_new")).toBe("something_new");
    expect(reasonLabel(null)).toBe("");
    expect(blankToNull("  ")).toBeNull();
    expect(blankToNull(" 22:00 ")).toBe("22:00");
  });
});
