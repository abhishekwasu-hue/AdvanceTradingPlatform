import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import { draftWithPrefill } from "../../fno/OrderTicketV2";
import { newDraft } from "../../fno/ticketModel";
import { FeaturesProvider } from "../../ui/useFeatures";
import prepSource from "./TicketPrepV2.tsx?raw";
import intentSource from "./intent.ts?raw";
import chatSource from "../chat/CopilotChatV2.tsx?raw";
import { TicketPrepV2 } from "./TicketPrepV2";
import { acceptPrefill, matchExpiry, parseTicketAsk, prefillChips } from "./intent";

/** M72 PR-9 (CP-9, F11): the Copilot prepares the ticket from his own words only; he places it himself. */

const render = (flags: Record<string, boolean>, q: string) => renderToStaticMarkup(
  <FeaturesProvider userKey={1} value={flags}><MemoryRouter><TicketPrepV2 question={q} /></MemoryRouter></FeaturesProvider>);

describe("M72 PR-9 ticket preparation", () => {
  it("reads the contract and his own values from his message", () => {
    const a = parseTicketAsk("Sell 2 lots NIFTY 25,000 CE at 85, stop 120 please");
    expect(a).toMatchObject({ underlying: "NIFTY", strike: 25000, right: "CE", side: "SELL", expiryWords: null });
    expect(a?.prefill).toEqual({ lots: 2, price: "85", stopLoss: "120", from: { lots: "typed", price: "typed", stopLoss: "typed" } });
    const b = parseTicketAsk("bank nifty 28 Nov 52000 put");
    expect(b).toMatchObject({ underlying: "BANKNIFTY", strike: 52000, right: "PE", side: null, expiryWords: "28 NOV" });
    expect(b?.prefill).toEqual({ from: {} });                                    // nothing typed: every field Not set
    expect(parseTicketAsk("NIFTY FUT 3 lots")).toMatchObject({ right: "FUT", strike: null, prefill: { lots: 3 } });
    expect(parseTicketAsk("What is NIFTY doing today?")).toBeNull();
    expect(parseTicketAsk("Explain open interest")).toBeNull();
  });

  it("a value that is not in his message (a model's) is dropped; the object's value only when it is the object's", () => {
    const q = "Sell NIFTY 25000 CE";
    expect(acceptPrefill({ lots: 5, price: "90", from: { lots: "typed", price: "typed" } }, q)).toEqual({ from: {} });
    expect(acceptPrefill({ stopLoss: "120", from: { stopLoss: "object" } }, q, [120])).toEqual({ stopLoss: "120", from: { stopLoss: "object" } });
    expect(acceptPrefill({ stopLoss: "130", from: { stopLoss: "object" } }, q, [120])).toEqual({ from: {} });
    expect(acceptPrefill({ lots: 2, from: {} }, "2 lots NIFTY 25000 CE")).toEqual({ from: {} });  // no source, no value
  });

  it("the expiry is his typed one only when it is on the list; otherwise he picks", () => {
    const list = ["2026-11-26", "2026-12-31"];
    expect(matchExpiry("28 NOV", list)).toBeNull();
    expect(matchExpiry("26 NOV", list)).toBe("2026-11-26");
    expect(matchExpiry("2026-12-31", list)).toBe("2026-12-31");
    expect(matchExpiry(null, list)).toBeNull();
  });

  it("the ticket starts from his defaults and his typed values, with the source of each", () => {
    expect(draftWithPrefill("BUY", undefined, null)).toEqual(newDraft("BUY"));
    const p = { lots: 2, stopLoss: "120", from: { lots: "typed" as const, stopLoss: "typed" as const } };
    expect(draftWithPrefill("SELL", { product: "NRML" }, p)).toEqual({ ...newDraft("SELL", { product: "NRML" }), lots: 2, stopLoss: "120" });
    expect(prefillChips(p).map((c) => c.text)).toEqual(["2 lots from your message", "stop-loss 120 from your message"]);
  });

  it("the card shows only with the flag and a contract; nothing is pre-picked; it never previews or places", () => {
    const q = "NIFTY 25000 CE 2 lots";
    expect(render({ ui_v2: true, copilot_ticket: false }, q)).toBe("");
    expect(render({ ui_v2: true, copilot_ticket: true }, "How is NIFTY?")).toBe("");
    const html = render({ ui_v2: true, copilot_ticket: true }, q);
    expect(html).toContain("data-ticket-prep");
    expect(html).toMatch(/data-ticket-open[^>]*disabled|disabled[^>]*data-ticket-open/);           // waits for his expiry and side
    expect(html).not.toMatch(/aria-checked="true"/);                                           // no side typed: none picked
    expect(html).toContain("2 lots from your message");
    for (const src of [prepSource, intentSource]) expect(src).not.toMatch(/\.(place|preview|placeMulti)\(/);
    expect(chatSource).toContain("!turn.answer.refused && <TicketPrepV2");
  });
});
