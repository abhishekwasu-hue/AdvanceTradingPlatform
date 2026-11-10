import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import SizeCalculatorV2 from "./SizeCalculatorV2";
import { EMPTY_SIZE_DRAFT, RISK_HINT, calculateSize, sizeBodyOf, sizeWords, type SizeDraft, type SizeResult } from "./sizeCalculator";

const SHARES: SizeDraft = { ...EMPTY_SIZE_DRAFT, account: "5,00,000", riskPct: "1", entry: "101", stop: "94", unit: "SHARES" };

describe("M94 R1 position-size calculator", () => {
  it("sends the typed values only; nothing is pre-filled", () => {
    expect(Object.values(EMPTY_SIZE_DRAFT).every((v) => v === "")).toBe(true);
    expect(sizeBodyOf(SHARES)).toEqual({ problem: null,
      body: { account: 500000, risk_pct: 1, risk_rupees: null, entry: 101, stop: 94, unit: "SHARES", lot_size: null } });
    expect(sizeBodyOf({ ...SHARES, unit: "LOTS", lotSize: "75" }).body).toMatchObject({ unit: "LOTS", lot_size: 75 });
    expect(sizeBodyOf({ ...SHARES, account: "", riskPct: "", riskRupees: "2000" }).body).toMatchObject({ account: null, risk_pct: null, risk_rupees: 2000 });
  });

  it.each([
    [{ entry: "0" }, "Entry price: enter a price above 0."],
    [{ stop: "-5" }, "Stop price: enter a price above 0."],
    [{ stop: "101" }, "The stop price is the same as the entry price: there is no stop distance to size on."],
    [{ riskPct: "" }, "Risk per trade: enter a % of the account or a rupee amount."],
    [{ riskPct: "0" }, "Risk per trade: the % must be more than 0 and at most 100."],
    [{ riskPct: "-1" }, "Risk per trade: the % must be more than 0 and at most 100."],
    [{ account: "0" }, "Account: enter an amount above 0 to use a % risk."],
    [{ account: "-100" }, "Account: enter an amount above 0 to use a % risk."],
    [{ riskRupees: "0" }, "Risk per trade: the rupee amount must be above 0."],
    [{ unit: "" }, "Size in: pick shares or F&O lots."],
    [{ unit: "LOTS", lotSize: "0" }, "Lot size: enter the contract's lot size, a whole number from 1."],
    [{ unit: "LOTS", lotSize: "2.5" }, "Lot size: enter the contract's lot size, a whole number from 1."],
    [{ entry: "abc" }, "Entry price: enter a price above 0."],
  ] as [Partial<SizeDraft>, string][])("refuses %o", (patch, problem) => {
    expect(sizeBodyOf({ ...SHARES, ...patch })).toEqual({ body: null, problem });
  });

  it("says the size first: whole lots with their quantity, or whole shares; less than one lot is no size", () => {
    const lots: SizeResult = { ok: true, unit: "LOTS", lot_size: 75, quantity: 225, lots: 3, risk_amount: 10000, per_share: 40, per_lot: 3000,
      at_risk: 9000, note: null };
    expect(sizeWords(lots)).toEqual({ size: "3 lots (225 qty)", detail: "Risks ₹9,000 of your ₹10,000 per trade · ₹3,000 per lot" });
    const shares: SizeResult = { ...lots, unit: "SHARES", lot_size: 1, quantity: 714, lots: 714, risk_amount: 5000, per_share: 7, per_lot: 7, at_risk: 4998 };
    expect(sizeWords(shares).size).toBe("714 shares");
    const none: SizeResult = { ...lots, quantity: 0, lots: 0, at_risk: 0, note: "Less than one lot. It is never rounded up." };
    expect(sizeWords(none)).toEqual({ size: "No size", detail: "Less than one lot. It is never rounded up." });
  });

  it("posts to the calculator endpoint", async () => {
    const calls: [string, RequestInit | undefined][] = [];
    const fetcher = (async (path: string, init?: RequestInit) => { calls.push([path, init]); return { ok: true }; }) as never;
    await calculateSize({ entry: 1 }, fetcher);
    expect(calls[0][0]).toBe("/risk-settings/size-calculator");
    expect(calls[0][1]?.method).toBe("POST");
  });

  it("is closed until opened, with empty fields and the hint worded without the study's name", () => {
    expect(RISK_HINT).toBe("reference 1% (study: at most 2%)");
    const closed = renderToStaticMarkup(<SizeCalculatorV2 />);
    expect(closed).toContain("Open the calculator");
    expect(closed).not.toContain("size-calculator-form");
    const open = renderToStaticMarkup(<SizeCalculatorV2 initiallyOpen />);
    expect(open).toContain("Hint: reference 1% (study: at most 2%)");
    expect(open).toContain("Places and saves nothing");
    expect(open.match(/value=""/g)?.length).toBeGreaterThanOrEqual(5);      // nothing pre-filled
    expect(open).not.toContain("Lot size");                                  // asked only for F&O lots
    const lots = renderToStaticMarkup(<SizeCalculatorV2 initiallyOpen initialDraft={{ ...SHARES, unit: "LOTS", lotSize: "75" }}
      initialResult={{ ok: true, unit: "LOTS", lot_size: 75, quantity: 225, lots: 3, risk_amount: 10000, per_share: 40, per_lot: 3000, at_risk: 9000, note: null }} />);
    expect(lots).toContain("Lot size");
    expect(lots).toContain("3 lots (225 qty)");
  });
});
