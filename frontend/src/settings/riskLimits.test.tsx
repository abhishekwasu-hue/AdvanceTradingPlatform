import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import RiskBlockV2 from "../risk/RiskBlockV2";
import { blockedBy } from "../fno/ticketModel";
import { SETTINGS_PATH, settingsSectionAt } from "./paths";
import { bodyOf, draftOf, onCount, valueWords, type RiskBlock, type RiskLimitsView } from "./riskLimits";

const VIEW: RiskLimitsView = {
  checks: [
    { id: "max_lots_per_order", label: "Max lots per order", help: "An order above this size is not placed.", group: "main", enabled: false, saved: false,
      fields: [{ key: "max_lots_per_order", label: "Lots", unit: "lots", min: 1, max: 100000, whole: true, optional: false, value: null }] },
    { id: "daily_loss", label: "Daily loss limit", help: "Once today's loss reaches it, no new entry today.", group: "main", enabled: true, saved: true,
      fields: [{ key: "max_daily_loss_pct", label: "% of account", unit: "%", min: 0.1, max: 100, whole: false, optional: true, value: 2 },
               { key: "daily_loss_rupees", label: "Rupees", unit: "₹", min: 1, max: 1e9, whole: false, optional: true, value: 2000 }] },
  ],
  capital: null, loss_streak_pause: "next_session", on: 1, exits: "Exits are never checked.",
};

describe("M64 Settings › Risk limits model", () => {
  it("lives at /settings/risk-limits, with today's Risk page as the address with ui_v2 off", () => {
    expect(SETTINGS_PATH["risk-limits"]).toBe("/settings/risk-limits");
    expect(settingsSectionAt("/settings/risk-limits")?.old).toBe("/risk-management");
  });

  it("sends only the checks that changed, and needs a value to switch one on", () => {
    const draft = draftOf(VIEW);
    expect(bodyOf(VIEW, draft).body).toEqual({ checks: {}, capital: null, loss_streak_pause: "next_session", opposite_positions: null });
    draft.checks.max_lots_per_order.enabled = true;
    expect(bodyOf(VIEW, draft).problem).toBe("Max lots per order: enter a value to switch it on.");
    draft.checks.max_lots_per_order.values.max_lots_per_order = "5";
    expect(bodyOf(VIEW, draft).body?.checks).toEqual({ max_lots_per_order: { enabled: true, values: { max_lots_per_order: 5 } } });
    draft.checks.max_lots_per_order.values.max_lots_per_order = "2.5";
    expect(bodyOf(VIEW, draft).problem).toBe("Max lots per order: enter a whole number.");
  });

  it("M68 PR-5: the pause end and opposite positions start Not set and the pause end is asked when needed", () => {
    const cool: RiskLimitsView = { ...VIEW, loss_streak_pause: null, opposite_positions: null, checks: [...VIEW.checks,
      { id: "loss_cooldown", label: "Cool-down after losses in a row", help: "", group: "main", enabled: false, saved: false, fields: [
        { key: "max_consecutive_losses", label: "Losses in a row", unit: "losses", min: 1, max: 1e9, whole: true, optional: false, value: null, min_open: false },
        { key: "loss_cooldown_minutes", label: "Pause", unit: "minutes", min: 1, max: 1e9, whole: true, optional: true, value: null, min_open: false }] },
      { id: "min_reward_risk", label: "Minimum reward:risk", help: "", group: "main", enabled: false, saved: false, fields: [
        { key: "min_risk_reward", label: "Reward for 1 risk", unit: "x", min: 0, max: 1e9, whole: false, optional: false, value: null, min_open: true }] }] };
    const draft = draftOf(cool);
    expect([draft.pause, draft.opposite]).toEqual(["", ""]);
    expect(bodyOf(cool, draft).body).toMatchObject({ loss_streak_pause: null, opposite_positions: null });
    draft.checks.loss_cooldown = { enabled: true, values: { max_consecutive_losses: "3", loss_cooldown_minutes: "" } };
    expect(bodyOf(cool, draft).problem).toBe("Cool-down after losses in a row: with no minutes set, pick when the pause ends.");
    draft.pause = "until_resume";
    draft.opposite = "deny";
    expect(bodyOf(cool, draft).body).toMatchObject({ loss_streak_pause: "until_resume", opposite_positions: "deny" });
    draft.checks.min_reward_risk = { enabled: true, values: { min_risk_reward: "0" } };
    expect(bodyOf(cool, draft).problem).toBe("Minimum reward:risk: reward for 1 risk must be more than 0.");
    draft.checks.min_reward_risk.values.min_risk_reward = "25";        // no ATP top (TS1-050)
    expect(bodyOf(cool, draft).problem).toBeNull();
  });

  it("says the values in words and how many checks are on", () => {
    expect(valueWords(VIEW.checks[1])).toBe("2% · ₹2,000");
    expect(onCount(VIEW)).toBe("1 of 2 on");
    expect(onCount({ ...VIEW, on: 0 })).toBe("All checks are off");
  });
});

describe("M64 RM-3 block message", () => {
  const block: RiskBlock = { check: "max_lots_per_order", text: "Max lots per order is 5 - this order is 8 lots. Place 5 lots?", admin: false,
    link: "/settings/risk-limits#max_lots_per_order", link_text: "Change limit", smaller_lots: 5 };

  it("shows the sentence, the smaller size and the Change limit link", () => {
    const html = renderToStaticMarkup(<MemoryRouter><RiskBlockV2 block={block} onSmaller={() => {}} /></MemoryRouter>);
    expect(html).toContain("Max lots per order is 5 - this order is 8 lots. Place 5 lots?");
    expect(html).toContain("Use 5 lots");
    expect(html).toContain('href="/settings/risk-limits#max_lots_per_order"');
    expect(html).toContain("Change limit");
  });

  it("says Ask your admin for an organisation ceiling, with no link", () => {
    const html = renderToStaticMarkup(<MemoryRouter><RiskBlockV2 block={{ ...block, admin: true, link: null, smaller_lots: null }} /></MemoryRouter>);
    expect(html).toContain("Ask your admin");
    expect(html).not.toContain("href=");
  });

  it("the ticket's own-limit line links to Settings › Risk limits, never for an admin ceiling", () => {
    expect(blockedBy({ can_place: false, errors: [], refusals: ["You hold 3 open positions; your limit is 3. Exit one before a new entry."] })?.link)
      .toBe("/settings/risk-limits");
    expect(blockedBy({ can_place: false, errors: [], refusals: ["Your organisation's limit on open positions: 4 exceeds limit 3. Ask your admin."] })?.link)
      .toBeNull();
  });
});
