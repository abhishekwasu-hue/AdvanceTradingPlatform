import { describe, expect, it } from "vitest";
import { answerRow } from "./model";
describe("UI-28 U2.4 answer lines as rows", () => {
  it("a 'Label: value' line is a row; a signed value carries its own direction; a sentence stays text", () => {
    expect(answerRow("Net P&L: +₹1,200")).toEqual({ label: "Net P&L", value: "+₹1,200", dir: "up" });
    expect(answerRow("Change: −0.8%")).toEqual({ label: "Change", value: "−0.8%", dir: "down" });
    expect(answerRow("PCR: 0.84")).toEqual({ label: "PCR", value: "0.84", dir: null });
    expect(answerRow("Trend: up on the daily")).toEqual({ label: "Trend", value: "up on the daily", dir: null });
    expect(answerRow("NIFTY closed higher today on broad buying.")).toBeNull();
    expect(answerRow("Note: this is what the method reads, nothing more.")).toBeNull();
  });
});
