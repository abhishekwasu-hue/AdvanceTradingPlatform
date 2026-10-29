import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ContractPreview, DeploymentCreateRequest, ResolvedContract } from "../types";
import ContractPreviewV2, { contractText, previewRows, rulesOf } from "./ContractPreviewV2";
import { paperBotRequest } from "./model";

const ce = { kind: "OPTION", underlying: "NIFTY", tradingsymbol: "NIFTY26N0324500CE", strike: 24500, right: "CE", expiry: "2026-11-03", lot_size: 75 } as ResolvedContract;
const pe = { ...ce, tradingsymbol: "NIFTY26N0324500PE", right: "PE" } as ResolvedContract;

describe("UI-28 U7.4: the contract a paper bot would trade", () => {
  it("reads the server's resolved contract as one line", () => {
    expect(contractText(ce, 1)).toBe("NIFTY 24500 CE · 3 Nov · 1 lot (75)");
    expect(contractText(pe, 2)).toBe("NIFTY 24500 PE · 3 Nov · 2 lots (150)");
  });
  it("one row per signal side the server could resolve; an error side is left out", () => {
    const p = { symbol: "NIFTY", kind: "OPTION", contracts: { LONG: ce, SHORT: { error: "no strike" } } } as ContractPreview;
    expect(previewRows(p, 1)).toEqual([{ side: "LONG", text: "NIFTY 24500 CE · 3 Nov · 1 lot (75)" }]);
  });
  it("sends only the bot's contract rules (the same request Start on paper sends)", () => {
    const req = paperBotRequest("orb_15m_5m", "NIFTY", null);
    expect(rulesOf(req)).toEqual({ instrument_kind: "OPTION", option_position: "BUY", expiry_rule: "NEAREST", strike_rule: "ATM", strike_offset: 0, max_lots: 1 });
  });
  it("offers the preview for an option bot, never for a cash symbol", () => {
    expect(renderToStaticMarkup(<ContractPreviewV2 request={paperBotRequest("orb_15m_5m", "NIFTY", null)} />)).toContain("See the contract it would trade");
    expect(renderToStaticMarkup(<ContractPreviewV2 request={paperBotRequest("orb_15m_5m", "RELIANCE", null) as DeploymentCreateRequest} />)).toBe("");
  });
});
