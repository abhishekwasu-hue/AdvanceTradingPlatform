import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import routesSource from "../routes.ts?raw";
import { PAGE_COMPONENTS_V2 } from "../shell/pagesV2";
import { orderFillRows, orderTiles } from "./model";
import ordersSource from "./OrdersV2.tsx?raw";
import { OrderRowV2 } from "./OrdersV2";
import { trade } from "./trading.fixture";

/** UX-10 My trades wave: My trades › Orders in the v2 shell - one fill per row, IST times, plain words, read-only. */

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const closed = trade({ id: 2, symbol: "DEMO B", direction: "SHORT", exit_time: "2026-10-09T05:30:00Z", exit_price: 90, exit_reason: "TARGET1", pnl: 1_500 });
const open = trade({ id: 1, entry_time: "2026-10-08T04:00:00Z" });

describe("Orders fills", () => {
  it("an entry per trade and an exit once closed, newest first", () => {
    const rows = orderFillRows([open, closed]);
    expect(rows.map((r) => r.key)).toEqual(["2-exit", "1-entry", "2-entry"]);
    expect(rows[0]).toMatchObject({ kind: "Exit", symbol: "DEMO B", side: "Short", mode: "Paper", price: "90.00", reason: "Target1" });
    expect(rows[1]).toMatchObject({ kind: "Entry", reason: null, size: "2 lots (150)" });
  });

  it("times in IST (en-IN), never the browser locale", () => {
    const [row] = orderFillRows([open]);
    expect(row.when).toContain("09:30");
    expect(row.when).not.toMatch(/AM|PM|\d+\/\d+\/\d{4}/);
  });

  it("tiles: fills, entries, exits, still open", () => {
    expect(orderTiles(orderFillRows([open, closed]))).toEqual({ fills: 3, entries: 2, exits: 1, open: 1 });
    expect(orderTiles([])).toEqual({ fills: 0, entries: 0, exits: 0, open: 0 });
  });

  it("a row shows the symbol, Entry / Exit, the side, size, price and time on two short lines", () => {
    const html = renderToStaticMarkup(<ul><OrderRowV2 row={orderFillRows([closed])[0]} /></ul>);
    const t = text(html);
    expect(t).toContain("DEMO B");
    expect(t).toContain("Exit");
    expect(t).toContain("at 90.00");
    expect(html).not.toContain("<table");
  });
});

describe("Orders page wiring and wording", () => {
  it("the v2 shell has it; today's router keeps today's page", () => {
    expect(PAGE_COMPONENTS_V2.orders).toBeDefined();
    expect(routesSource).toContain('orders: () => import("./pages/OrdersPage")');
  });

  it("UX-11 U5: the page's one footer line, as on Positions", () => {
    expect(ordersSource).toContain("<PageFootnote />");   // UI-28 U1.7: no "Phase 0" jargon
  });

  it("read-only, no jargon, no table", () => {
    expect(ordersSource).toContain("api.listNewestTrades");           // REV-1 M3: the newest page of /trades
    expect(ordersSource).not.toMatch(/api\.(place|cancel|modify|exit|close)/i);
    expect(ordersSource).not.toMatch(/blotter|<table|ENTRY"|EXIT"/);
  });
});
