import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { BrokerBook, BrokerBookAccount } from "../types";
import { cardTextProblems } from "../ui/textBudget";
import { BrokerBookView, type BrokerBookLoad } from "./BrokerBookV2";
import ordersSource from "./OrdersV2.tsx?raw";
import portfolioSource from "./PortfolioV2.tsx?raw";
import positionsSource from "./PositionsV2.tsx?raw";
import bookSource from "./BrokerBookV2.tsx?raw";

/** Owner 14 Nov "Ho" (flag broker_book): "From your broker" - his own broker's lists, read-only. */

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => {};

const ZERODHA: BrokerBookAccount = {
  broker: "zerodha", broker_label: "Zerodha (Kite)", account_label: null, age_seconds: 0,
  positions: { message: null, rows: [
    { symbol: "NIFTY26NOVFUT", exchange: "NFO", product: "NRML", quantity: -75, side: "Short", average_price: 24000.46, ltp: 23900, pnl: 7534.2 },
    { symbol: "TCS", exchange: "NSE", product: "CNC", quantity: 0, side: "Closed", average_price: 3500, ltp: 3510, pnl: -20 },
  ] },
  holdings: { message: null, rows: [{ symbol: "INFY", exchange: "NSE", quantity: 10, average_price: 1500, ltp: 1550, value: 15500, pnl: 500 }] },
  orders: { message: null, rows: [
    { symbol: "TCS", exchange: "NSE", side: "Sell", quantity: 5, filled_quantity: 0, order_type: "LIMIT", product: "CNC", status: "OPEN", price: 3600, average_price: null, placed_at: "2026-11-14T05:00:00+00:00" },
  ] },
};
const UPSTOX: BrokerBookAccount = {
  broker: "upstox", broker_label: "Upstox", account_label: null, age_seconds: 3,
  positions: { rows: [], message: "Upstox needs your static IP for this list. Register ATP's static IP with Upstox (steps on the Broker page)." },
};
const SHOONYA: BrokerBookAccount = { broker: "shoonya", broker_label: "Shoonya", account_label: "family", age_seconds: 0, positions: { rows: [], message: null } };

const ok = (accounts: BrokerBookAccount[], parts: BrokerBook["parts"] = ["positions"]): BrokerBookLoad => ({ status: "ok", data: { parts, accounts } });
const view = (part: "positions" | "holdings" | "orders", load: BrokerBookLoad) =>
  renderToStaticMarkup(<BrokerBookView part={part} load={load} busy={false} onRefresh={noop} onConnect={noop} />);

describe("From your broker", () => {
  it("lists the broker's positions read-only, coloured by P&L, with no exit or modify button", () => {
    const html = view("positions", ok([ZERODHA]));
    expect(text(html)).toContain("From your broker");
    expect(html.match(/data-broker-row="position"/g)).toHaveLength(2);
    expect(text(html)).toContain("NIFTY26NOVFUT");
    expect(text(html)).toContain("Qty 75 · NRML · NFO");
    expect(html).toContain("text-up");
    expect(html).toContain("text-down");
    expect(html).not.toMatch(/data-testid="(exit-now|exit-part|reverse|roll)"/);
    expect(text(html)).not.toMatch(/\b(Exit|Modify|Cancel)\b/);
    const buttons = [...html.matchAll(/<button[^>]*>([\s\S]*?)<\/button>/g)].map((m) => text(m[1]).trim());
    expect(buttons).toEqual(["Refresh"]);
  });

  it("links to the broker's own app only where the #1074 table knows it", () => {
    const html = view("positions", ok([ZERODHA, SHOONYA]));
    expect(html).toContain('href="https://kite.zerodha.com"');
    expect(html).toContain('rel="noopener noreferrer"');
    expect(html.match(/data-broker-app/g)).toHaveLength(1);                // Shoonya: not in the table, no link
    expect(text(html)).toContain("Shoonya · family");
    expect(text(html)).toContain("No positions at Shoonya.");
  });

  it("holdings and today's orders", () => {
    const h = text(view("holdings", ok([ZERODHA], ["holdings"])));
    expect(h).toContain("INFY");
    expect(h).toContain("value ₹15,500");
    const o = view("orders", ok([ZERODHA], ["orders"]));
    expect(text(o)).toContain("0 of 5 filled · LIMIT · CNC · at ₹3,600");
    expect(text(o)).toContain("10:30 IST");
    expect(text(o)).toContain("open");
  });

  it("a refusal is the broker's plain sentence in place of the list", () => {
    const html = view("positions", ok([UPSTOX]));
    expect(html).toContain('role="alert"');
    expect(text(html)).toContain("Upstox needs your static IP for this list.");
    expect(html).not.toContain("data-broker-row");
  });

  it("no broker linked, loading and a failed load", () => {
    expect(text(view("orders", ok([], ["orders"])))).toContain("Connect your broker to see its own orders today here.");
    expect(view("positions", { status: "loading" })).toContain('aria-busy="true"');
    expect(text(view("positions", { status: "error" }))).toContain("We could not load your broker's list.");
  });

  it("one short visible line", () => {
    const html = view("positions", ok([ZERODHA]));
    const intro = text(html.split("<p")[1].split("</p>")[0].replace(/^[^>]*>/, ""));
    expect(cardTextProblems([{ card: "From your broker", lines: [intro] }])).toEqual([]);
  });

  it("each page shows its own list only with the flag, and the card calls no order route", () => {
    expect(positionsSource).toContain('features.on("broker_book") && signedIn && <BrokerBookV2 part="positions" />');
    expect(portfolioSource).toContain('features.on("broker_book") && <BrokerBookV2 part="holdings" />');
    expect(ordersSource).toContain('features.on("broker_book") && <BrokerBookV2 part="orders" />');
    expect(bookSource).toContain("api.brokerBook(");
    expect(bookSource).not.toMatch(/api\.(place|modify|cancel|exit|close)\w*\(/i);
  });
});
