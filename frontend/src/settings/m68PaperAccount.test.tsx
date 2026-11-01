import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/errors";
import { CAPITAL_KEY, CapitalAsk, NEEDS_CAPITAL, capitalOf, needsCapital, saveCapital } from "./CapitalAsk";
import { paperChargesOf, type TradeField, type TradeSettingsView } from "./tradeSettings";
import { riskStart } from "../setup/StepsV2";
import { RISK_DEFAULTS, riskErrors } from "../setup/model";

/** M68 PR-7 (TS1-064 .. 071, owner decision D4): no starting capital, brokerage or slippage is pre-chosen. The first paper
 *  order asks for the capital (with "Save as my default"); Setup starts it empty; his saved charges start the Options
 *  Builder's brokerage. */

const field = (key: string, value: unknown, saved = true): TradeField => ({
  key, label: key, kind: "number", help: "", unit: "", choices: [], low: null, high: null, low_open: false, term: null, fact: false,
  wired: true, scope: "user", value, saved,
});
const view = (fields: TradeField[]): TradeSettingsView => ({ admin: false, page: "/settings/trade-settings",
  sections: [{ id: "paper_account", title: "Paper account", fields, today: null }] });

describe("M68 PR-7: the starting capital is asked, never filled in", () => {
  it("recognises the server's refusal and reads what he typed", () => {
    expect(needsCapital([`${NEEDS_CAPITAL} (Settings › Trade settings › Paper account)`])).toBe(true);
    expect(needsCapital(["Something else"])).toBe(false);
    expect(needsCapital(undefined)).toBe(false);
    expect(capitalOf("2,50,000")).toBe(250000);
    expect(capitalOf("5000")).toBe(5000);                          // no ATP floor
    expect(capitalOf("0")).toBeNull();
    expect(capitalOf("")).toBeNull();
    expect(capitalOf("abc")).toBeNull();
  });

  it("the ask is an empty field with Save as my default - nothing is pre-filled", () => {
    const html = renderToStaticMarkup(<CapitalAsk onSaved={() => undefined} />);
    expect(html).toContain('placeholder="Not set"');
    expect(html).toContain('value=""');
    expect(html).toContain("Save as my default");
    expect(html).not.toMatch(/checked=""/);
    expect(html).not.toMatch(/1,00,000|10,00,000/);
  });

  it("saves to his account; ticked also as his default; with no paper account to his Trade settings", async () => {
    const setPaperCapital = vi.fn().mockResolvedValue({});
    const saveDefault = vi.fn().mockResolvedValue(true);
    expect(await saveCapital(300000, false, { setPaperCapital, saveDefault })).toBe("account");
    expect(setPaperCapital).toHaveBeenCalledWith(300000);
    expect(saveDefault).not.toHaveBeenCalled();
    expect(await saveCapital(300000, true, { setPaperCapital, saveDefault })).toBe("account");
    expect(saveDefault).toHaveBeenCalledWith(CAPITAL_KEY, 300000);
    const off = vi.fn().mockRejectedValue(new ApiError(404, "Not found"));
    const keep = vi.fn().mockResolvedValue(true);
    expect(await saveCapital(50000, false, { setPaperCapital: off, saveDefault: keep })).toBe("settings");
    expect(keep).toHaveBeenCalledWith(CAPITAL_KEY, 50000);
    const locked = vi.fn().mockRejectedValue(new ApiError(409, "Locked", "fixed once your PAPER account has a trade"));
    await expect(saveCapital(50000, true, { setPaperCapital: locked, saveDefault: keep })).rejects.toBeInstanceOf(ApiError);
  });

  it("Setup starts the capital empty unless his account has one (no ₹1,00,000 of ATP's)", () => {
    expect(RISK_DEFAULTS).toEqual({});
    expect(riskStart(null, null).capital).toBe("");
    expect(riskStart(null, 250000).capital).toBe("2,50,000");
    const empty = { capital: "", riskPerTradePct: "", dailyLossPct: "", maxTradesPerDay: "", minRr: "" };
    expect(riskErrors(empty)).toEqual({});
  });
});

describe("M68 PR-7: his own paper charges", () => {
  it("reads only what he saved; brokerage only with 'Statutory charges + my brokerage'", () => {
    expect(paperChargesOf(null)).toEqual({});
    expect(paperChargesOf(view([field("paper_account.brokerage_per_order", 20, false)]))).toEqual({});
    expect(paperChargesOf(view([field("paper_account.charges_model", "STATUTORY"), field("paper_account.brokerage_per_order", 20)]))).toEqual({});
    expect(paperChargesOf(view([field("paper_account.charges_model", "STATUTORY_BROKERAGE"), field("paper_account.brokerage_per_order", 15),
      field("paper_account.slippage_pct", 0.05), field("paper_account.capital", 400000)]))).toEqual({ brokeragePerOrder: 15, slippagePct: 0.05, capital: 400000 });
  });
});
