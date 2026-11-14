import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { OrderModeChoice } from "../fno/OrderModeV2";
import type { TradeSettingsView } from "../settings/tradeSettings";
import actionSource from "./BrokerAppAction.tsx?raw";
import { BrokerAppAction, BrokerFactsBody, type BrokerAppInfo } from "./BrokerAppAction";
import { basketFacts, entryFacts, ownEntrySettings, reverseFacts, ticketFacts } from "./brokerFacts";
import { BROKER_TABLE, KITE_BASKET_URL, brokerRow, factRows, factsText, kiteEntryLink, type OrderFacts } from "./brokerLinks";
import ticketSource from "../fno/OrderTicketV2.tsx?raw";
import basketSource from "../fno/BasketPanelV2.tsx?raw";
import reverseSource from "../fno/PositionActionsV2.tsx?raw";
import signalSource from "../signals/SignalPartsV2.tsx?raw";
import boardSource from "../screener/ResultBoard.tsx?raw";

/**
 * Route 3 (BIZ-1 B2-B4, owner 14 Nov): "Trade in your broker app" on the ticket, the basket, Reverse, signal cards and
 * scan matches. The user places the order himself on the broker's own page; every fact comes from his own entered
 * order or his own saved settings, never from the engine; a URL is never guessed.
 */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");

function settings(values: Record<string, unknown>): TradeSettingsView {
  return { admin: false, page: "", sections: [{ id: "s", title: "S", today: null, fields: Object.entries(values).map(([key, value]) => ({
    key, label: key, kind: "choice", help: "", unit: "", choices: [], low: null, high: null, low_open: false, term: null, fact: false, wired: true,
    scope: "user", value, saved: true })) }] } as unknown as TradeSettingsView;
}

const ZERODHA: BrokerAppInfo = { on: true, broker: "zerodha", publisher_api_key: "pub-key" };

describe("B4 the broker table", () => {
  it("official https homes only; only Kite has a verified pre-filled format", () => {
    for (const b of BROKER_TABLE) {
      expect(b.home).toMatch(/^https:\/\/[a-z.]+$/);              // a home, never an order path
      expect(b.prefill).toBe(b.id === "zerodha" ? "verified" : "not verified");
    }
    expect(BROKER_TABLE.map((b) => b.id)).toEqual(["zerodha", "angelone", "upstox", "fyers", "dhan", "groww", "kotak"]);
  });

  it("maps ATP's stored broker names", () => {
    expect(brokerRow("angel_one")?.label).toBe("Angel One");
    expect(brokerRow("KOTAK_NEO")?.label).toBe("Kotak Neo");
    expect(brokerRow("shoonya")).toBeNull();
    expect(brokerRow(null)).toBeNull();
  });
});

describe("B2 / B3 facts come only from his own order or settings", () => {
  it("ticket: his lots x lot size; a price only for his own Limit order", () => {
    const target = { underlying: "NIFTY", label: "NIFTY 28 OCT 22800 CE", lotSize: 75 };
    const market = ticketFacts(target, { side: "BUY", lots: 2, orderType: "MARKET", product: "NRML", price: "120", trigger: "" });
    expect(market).toMatchObject({ instrument: "NIFTY 28 OCT 22800 CE", side: "BUY", lots: 2, quantity: 150, orderType: "MARKET", product: "NRML", price: null });
    expect(ticketFacts(target, { side: "SELL", lots: 1, orderType: "LIMIT", product: null, price: "120.5", trigger: "" }).price).toBe(120.5);
    expect(ticketFacts(target, { side: "SELL", lots: null, orderType: null, product: null, price: "", trigger: "" }))
      .toMatchObject({ quantity: null, orderType: null, product: null });
  });

  it("signal: the side of his signal's direction; size, type, product only from his saved settings; never a level", () => {
    const own = ownEntrySettings(settings({ "position_size.mode": "FIXED_QUANTITY", "position_size.quantity": 10, "order_defaults.order_type": "MARKET",
      "order_defaults.product": "MIS" }));
    const f = entryFacts({ symbol: "RELIANCE", exchange: "NSE", direction: "LONG" }, own);
    expect(f).toEqual({ instrument: "RELIANCE", exchange: "NSE", side: "BUY", quantity: 10, orderType: "MARKET", product: "MIS", price: null, legs: undefined });
    expect(JSON.stringify(f)).not.toMatch(/stop|target|entry/i);
    const none = entryFacts({ symbol: "RELIANCE", direction: "SHORT" }, ownEntrySettings(null));
    expect(none).toMatchObject({ side: "SELL", quantity: null, orderType: null, product: null });
    expect(entryFacts({ symbol: "NIFTY 50", direction: "LONG", index: true }, own)).toMatchObject({ exchange: null, quantity: null });
    // Fixed lots is not a stock quantity
    expect(ownEntrySettings(settings({ "position_size.mode": "FIXED_LOTS", "position_size.lots": 2 }))).toMatchObject({ quantity: null, lots: 2 });
  });

  it("basket and Reverse: his legs; Reverse is the new side of his held quantity", () => {
    const b = basketFacts([{ side: "SELL", lots: 1, name: "NIFTY 28 OCT 22800 CE" }, { side: "BUY", lots: 2, name: "NIFTY 28 OCT 23000 CE" }], "NRML");
    expect(b.legs).toEqual(["Sell 1 lot NIFTY 28 OCT 22800 CE", "Buy 2 lots NIFTY 28 OCT 23000 CE"]);
    expect(reverseFacts({ symbol: "TCS", exchange: "NSE", direction: "LONG", quantity: 5, instrument_kind: "UNDERLYING" }, null))
      .toMatchObject({ side: "SELL", quantity: 5, exchange: "NSE", orderType: "MARKET" });
  });
});

describe("B4 the Kite pre-filled link and the copy sheet", () => {
  const base: OrderFacts = { instrument: "RELIANCE", exchange: "NSE", side: "BUY", quantity: 10, orderType: "MARKET", product: "MIS", price: null };

  it("Kite pre-fills a plain stock Market order of his quantity, and nothing else", () => {
    const link = kiteEntryLink(base, "pub-key")!;
    expect(link.action).toBe(KITE_BASKET_URL);
    expect(JSON.parse(link.fields.data)).toEqual([{ variety: "regular", tradingsymbol: "RELIANCE", exchange: "NSE", transaction_type: "BUY",
      order_type: "MARKET", quantity: 10, readonly: false }]);
    expect(kiteEntryLink({ ...base, orderType: "LIMIT", price: 100 }, "pub-key")).toBeNull();   // the verified format carries no price
    expect(kiteEntryLink({ ...base, quantity: null }, "pub-key")).toBeNull();
    expect(kiteEntryLink({ ...base, side: null }, "pub-key")).toBeNull();
    expect(kiteEntryLink({ ...base, exchange: null }, "pub-key")).toBeNull();                    // F&O / index: no cash order
    expect(kiteEntryLink(base, "")).toBeNull();
  });

  it("the sheet lists his facts with Copy; a fact he did not set reads 'Your choice' and has no Copy", () => {
    const html = renderToStaticMarkup(<BrokerFactsBody facts={{ ...base, product: null }} broker="angel_one" onCopy={() => undefined} />);
    const words = text(html);
    expect(words).toContain("Symbol RELIANCE (NSE)");
    expect(words).toContain("Quantity 10");
    expect(words).toContain("Order type Market");
    expect(words).toContain("Product Your choice");
    expect(html).not.toContain('data-copy="Product"');
    expect(html).toContain('href="https://www.angelone.in"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html.match(/data-broker-home=/g)).toHaveLength(1);
    expect(factsText({ ...base, product: null })).toBe("Symbol: RELIANCE (NSE)\nSide: Buy\nQuantity: 10\nOrder type: Market");
  });

  it("an unknown broker shows every broker's own home", () => {
    const html = renderToStaticMarkup(<BrokerFactsBody facts={base} broker={null} onCopy={() => undefined} />);
    expect(html.match(/data-broker-home=/g)).toHaveLength(BROKER_TABLE.length);
  });

  it("legs replace side / quantity rows", () => {
    expect(factRows({ ...base, legs: ["Sell 1 lot NIFTY 28 OCT 22800 CE"] }).map((r) => r.name)).toEqual(["Symbol", "Leg 1", "Order type", "Product"]);
  });
});

describe("B2 / B3 the button", () => {
  it("nothing while the feature is off; the label never says buy or sell", () => {
    expect(renderToStaticMarkup(<BrokerAppAction info={null} facts={{ instrument: "X", side: "BUY", quantity: 1, orderType: null, product: null, price: null }} testId="t" />)).toBe("");
    const html = renderToStaticMarkup(<BrokerAppAction info={ZERODHA} facts={{ instrument: "X", side: "BUY", quantity: 1, orderType: null, product: null, price: null }} testId="t" />);
    expect(text(html).trim()).toBe("Trade in your broker app");
    expect(html).toContain('data-testid="t-broker-app"');
  });

  it("the dead LIVE choice becomes the action while LIVE is not open for him, and only then", () => {
    const action = <span data-x>action</span>;
    const off = renderToStaticMarkup(<OrderModeChoice mode="LIVE" onMode={() => undefined} live={{ open: false, text: "LIVE is off." }} testId="m" brokerApp={action} />);
    expect(off).toContain("data-x");
    expect(off).toContain("LIVE is off.");
    expect(renderToStaticMarkup(<OrderModeChoice mode="LIVE" onMode={() => undefined} live={{ open: true, text: null }} testId="m" brokerApp={action} />)).not.toContain("data-x");
    expect(renderToStaticMarkup(<OrderModeChoice mode="PAPER" onMode={() => undefined} live={{ open: false, text: "x" }} testId="m" brokerApp={action} />)).not.toContain("data-x");
  });

  it("is wired on the ticket, the basket, Reverse, signals and scan matches; nothing places an order", () => {
    expect(ticketSource).toContain("<BrokerAppAction info={brokerApp} testId=\"ticket\"");
    expect(basketSource).toContain("<BrokerAppAction info={brokerInfo} testId=\"basket\"");
    expect(reverseSource).toContain("<BrokerAppAction info={brokerApp} testId=\"reverse\"");
    expect(signalSource).toContain("<EntryBrokerApp testId=\"signal\"");
    expect(boardSource).toContain("<EntryBrokerApp compact testId=\"scan\"");
    expect(actionSource).not.toMatch(/method:\s*"(POST|PUT|PATCH|DELETE)"|placeOrder|\/orders/);
    expect(actionSource).toContain('features.on("ui_v2") && features.on("broker_app_links")');
  });
});
