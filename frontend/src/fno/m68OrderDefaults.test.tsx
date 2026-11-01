import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { TicketBody } from "./OrderTicketV2";
import { ORDER_TYPE_MISSING, PRODUCT_MISSING, draftProblems, firstMissing, newDraft, type TicketTarget } from "./ticketModel";
import { ORDER_TYPE_KEY, PRODUCT_KEY, orderDefaultsOf, saveAsDefault, type TradeField, type TradeSettingsView } from "../settings/tradeSettings";
import { SMART_DEFAULTS, readSmart, smartFormOf, smartOf } from "../settings/smartExecutionSettings";

/** M68 PR-1 (TS1-001 .. 016): the ticket pre-selects no order type and no product; it asks for each, focused, with
 *  "Save as my default"; his saved Order defaults start it; the smart-execution band has no starting value. */

const target = { underlying: "NIFTY", expiry: "2026-12-31", strike: 24800, right: "CE", label: "NIFTY 24800 CE", lotSize: 75, ltp: 120,
  bid: 119.5, ask: 120.5 } as TicketTarget;
const noop = () => undefined;
// M68 PR-2 asks for the lots first (fno/m68PositionSize.test.tsx); these PR-1 tests start with his lots entered.
const lotsIn = (): ReturnType<typeof newDraft> => ({ ...newDraft("BUY"), lots: 1 });
const body = (draft = lotsIn(), defaults?: Parameters<typeof TicketBody>[0]["defaults"], saveDefaults = true) => renderToStaticMarkup(
  <TicketBody target={target} draft={draft} step={{ kind: "edit" }} busy={false} problem={null} idPrefix="t" defaults={defaults} saveDefaults={saveDefaults}
    onDraft={noop} onReview={noop} onPlace={noop} onBack={noop} onClose={noop} />);

const field = (key: string, value: unknown, saved = true): TradeField => ({
  key, label: key, kind: "choice", help: "", unit: "", choices: [], low: null, high: null, low_open: false, term: null, fact: false,
  wired: true, scope: "user", value, saved,
});
const view = (fields: TradeField[]): TradeSettingsView => ({ admin: false, page: "/settings/trade-settings",
  sections: [{ id: "order_defaults", title: "Order defaults", fields, today: null }] });

describe("M68 PR-1: the ticket asks for what is Not set", () => {
  it("a new ticket pre-selects no order type and no product, and asks the order type first, then the product", () => {
    const d = lotsIn();
    expect(d.orderType).toBeNull();
    expect(d.product).toBeNull();
    expect(draftProblems(d)).toEqual([ORDER_TYPE_MISSING, PRODUCT_MISSING]);
    expect(firstMissing(d)).toBe("order-type");
    expect(firstMissing({ ...d, orderType: "MARKET" })).toBe("product");
    expect(firstMissing({ ...d, orderType: "LIMIT", product: "MIS" })).toBeNull();
  });

  it("the field it asks for is outlined and carries the id it focuses; nothing is checked; Review waits", () => {
    const html = body();
    expect(html).toMatch(/id="t-order-type"[^>]*data-asking="yes"|data-asking="yes"[^>]*id="t-order-type"/);
    expect(html).not.toMatch(/id="t-product"[^>]*data-asking="yes"/);
    expect(html).not.toMatch(/aria-checked="true"[^>]*data-option="(MARKET|LIMIT|SL|SL-M|MIS|NRML)"/);   // only the side is chosen
    expect(html).toContain('data-ticket="order-type-unset"');
    expect(html).toMatch(/disabled=""[^>]*data-ticket="review"|data-ticket="review"[^>]*disabled=""/);
    expect(html).not.toContain(`data-save-default="${ORDER_TYPE_KEY}"`);          // nothing picked yet: nothing to save
    const next = body({ ...lotsIn(), orderType: "MARKET" });
    expect(next).toMatch(/id="t-product"[^>]*data-asking="yes"|data-asking="yes"[^>]*id="t-product"/);
  });

  it("a picked order type and product each offer Save as my default (only with ui_v2, never for a value from his defaults)", () => {
    const html = body({ ...lotsIn(), orderType: "LIMIT", product: "NRML" });
    expect(html).toContain(`data-save-default="${ORDER_TYPE_KEY}"`);
    expect(html).toContain(`data-save-default="${PRODUCT_KEY}"`);
    expect(html).not.toMatch(/data-asking="yes"/);
    expect(html).not.toMatch(/disabled=""[^>]*data-ticket="review"/);
    expect(body({ ...lotsIn(), orderType: "LIMIT", product: "NRML" }, undefined, false)).not.toContain("data-save-default");
    const fromDefaults = body(newDraft("BUY", { orderType: "LIMIT", product: "NRML", lots: 1 }), { orderType: "LIMIT", product: "NRML", lots: 1 });
    expect(fromDefaults).not.toContain("data-save-default");
  });

  it("Save as my default writes that one field of his Trade settings, never an empty one", async () => {
    const fetcher = vi.fn().mockResolvedValue(view([]));
    expect(await saveAsDefault(PRODUCT_KEY, "MIS", fetcher as never)).toBe(true);
    expect(fetcher).toHaveBeenCalledWith("/me/trade-settings", { method: "PUT", body: JSON.stringify({ values: { [PRODUCT_KEY]: "MIS" } }) });
    expect(await saveAsDefault(ORDER_TYPE_KEY, null, fetcher as never)).toBe(false);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});

describe("M68 PR-1: his saved Order defaults start the ticket and the bot forms", () => {
  it("only the values he saved are read; a bot style is not a ticket type and the other way round", () => {
    expect(orderDefaultsOf(null)).toEqual({});
    expect(orderDefaultsOf(view([field(PRODUCT_KEY, null, false), field(ORDER_TYPE_KEY, null, false)]))).toEqual({});
    expect(orderDefaultsOf(view([field(PRODUCT_KEY, "NRML"), field(ORDER_TYPE_KEY, "SL-M")]))).toEqual({ product: "NRML", orderType: "SL-M" });
    expect(orderDefaultsOf(view([field(ORDER_TYPE_KEY, "PROTECTED_LIMIT"), field("order_defaults.price_protection_pct", 0.4),
      field("order_defaults.holding", "SWING")]))).toEqual({ botOrderStyle: "PROTECTED_LIMIT", protectionPct: 0.4, holding: "SWING" });
    expect(orderDefaultsOf(view([field(ORDER_TYPE_KEY, "MARKET")]))).toEqual({ orderType: "MARKET", botOrderStyle: "MARKET" });
  });
});

describe("M68 PR-1 (TS1-013): the smart-execution price band has no starting value", () => {
  it("Not set reads, shows and saves as empty / null", () => {
    expect(SMART_DEFAULTS.protection_band_pct).toBeNull();
    expect(readSmart({}).protection_band_pct).toBeNull();
    expect(smartFormOf(readSmart({})).protection_band_pct).toBe("");
    const out = smartOf(smartFormOf(readSmart({})));
    expect(out.problems).toEqual([]);
    expect(out.value.protection_band_pct).toBeNull();
    expect(smartOf({ ...smartFormOf(readSmart({})), protection_band_pct: "0.8" }).value.protection_band_pct).toBe(0.8);
    expect(readSmart({ protection_band_pct: 1.2 }).protection_band_pct).toBe(1.2);               // TS-6: his saved value is kept
  });
});
