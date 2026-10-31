import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readAllPages, readNewest, tradesPageQuery, TRADES_PAGE_MAX } from "./api/tradesPaging";
import { sharedRead } from "./api/sharedRead";
import { debounce } from "./utils/debounce";
import debounceSource from "./utils/debounce.ts?raw";
import { orderGroup } from "./terminal/api";
import { SOURCES, feedQuery } from "./signals/feed";
import { when as systemListWhen } from "./components/SystemListsCard";
import { shortDate } from "./admin/consoleApi";
import { dueLine } from "./admin/SupportDeskV2";
import { printTitle } from "./chartAnalyze/printResult";
import ordersSource from "./trading/OrdersV2.tsx?raw";
import dashboardSource from "./dashboard/useDashboardData.ts?raw";
import newsSource from "./pages/NewsEventsPage.tsx?raw";
import notificationsSource from "./pages/NotificationsPage.tsx?raw";
import { SUMMARY_REFRESH_MS, UNREAD_REFRESH_MS, REFRESH_MS } from "./dashboard/useDashboardData";
import type { SupportTicket } from "./admin/supportDeskApi";

/** REV-1 M3 (Low): paged /trades, fewer reads, IST times, the Cancelled group, the Signals source filter. */

const rows = (n: number, from = 0) => Array.from({ length: n }, (_, i) => from + i);

describe("/trades paging", () => {
  it("the page query is bounded to what the server accepts", () => {
    expect(tradesPageQuery(50)).toBe("/trades?limit=50");
    expect(tradesPageQuery(50, 100)).toBe("/trades?limit=50&offset=100");
    expect(tradesPageQuery(10_000)).toBe(`/trades?limit=${TRADES_PAGE_MAX}`);
    expect(tradesPageQuery(0, -5)).toBe("/trades?limit=1");
  });

  it("the whole history: page after page until a short page, each page read once", async () => {
    const asked: [number, number][] = [];
    const all = rows(1_234);
    const out = await readAllPages(async (limit, offset) => { asked.push([limit, offset]); return all.slice(offset, offset + limit); });
    expect(out).toEqual(all);
    expect(asked).toEqual([[500, 0], [500, 500], [500, 1000]]);
  });

  it("an exact multiple of the page ends on the empty page; the guard stops a runaway", async () => {
    const asked: number[] = [];
    await readAllPages(async (limit, offset) => { asked.push(offset); return rows(Math.max(0, Math.min(limit, 1000 - offset)), offset); });
    expect(asked).toEqual([0, 500, 1000]);
    const endless = await readAllPages(async (limit) => rows(limit), 10, 3);
    expect(endless).toHaveLength(30);
  });

  it("the newest N and whether older ones exist (reads N + 1)", async () => {
    const all = rows(120);
    const page = vi.fn(async (limit: number, offset: number) => all.slice(offset, offset + limit));
    expect(await readNewest(page, 50)).toEqual({ rows: rows(50), more: true });
    expect(page).toHaveBeenLastCalledWith(51, 0);
    expect(await readNewest(page, 120)).toEqual({ rows: all, more: false });
    expect(await readNewest(page, 200)).toEqual({ rows: all, more: false });
    const big = await readNewest(async (limit, offset) => rows(Math.min(limit, 2000 - offset), offset), 900);
    expect(big.rows).toHaveLength(900);
    expect(big.more).toBe(true);
  });

  it("Orders lists the newest trades and pages through with Show older trades", () => {
    expect(ordersSource).toContain("api.listNewestTrades(pages * ORDERS_TRADES_PAGE)");
    expect(ordersSource).toContain("Show older trades");
  });
});

describe("fewer reads", () => {
  beforeEach(() => { vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); });

  it("debounce: one call after the burst, with the last arguments; cancel drops it", () => {
    const fn = vi.fn();
    const d = debounce(fn, 400);
    d("N"); d("NI"); d("NIF");
    vi.advanceTimersByTime(399);
    expect(fn).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(fn).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledWith("NIF");
    d("X"); d.cancel();
    vi.advanceTimersByTime(1000);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("typing R, E, L 100 ms apart reads once, with REL (the News Symbol box)", () => {
    const reads: string[] = [];
    const read = debounce((q: string) => reads.push(q), 400);
    for (const q of ["R", "RE", "REL"]) { read(q); vi.advanceTimersByTime(100); }
    vi.advanceTimersByTime(400);
    expect(reads).toEqual(["REL"]);
    expect(debounceSource).toContain("export function useDebouncedValue");     // the hook form the page uses
  });

  it("News: the Symbol box reads through the debounced value", () => {
    expect(newsSource).toContain("useDebouncedValue(symbolFilter.trim(), NEWS_SEARCH_DEBOUNCE_MS)");
    expect(newsSource).toContain("symbol: symbolQuery || undefined");
    expect(newsSource).toContain("[categoryFilter, symbolQuery, originFilter]");
  });

  it("shared read: callers at the same time share one request; the next ask reads again", async () => {
    vi.useRealTimers();
    let n = 0;
    const load = () => new Promise<number>((r) => setTimeout(() => r(++n), 5));
    const [a, b] = await Promise.all([sharedRead("k", load), sharedRead("k", load)]);
    expect([a, b, n]).toEqual([1, 1, 1]);
    expect(await sharedRead("k", load)).toBe(2);
    const failing = () => Promise.reject(new Error("x"));
    await expect(sharedRead("bad", failing)).rejects.toThrow("x");
    expect(await sharedRead("bad", async () => 7)).toBe(7);           // a failure is not kept
  });

  it("Today: the 30-day summary is not re-read every 30 s; unread alerts come on the live stream", () => {
    expect(SUMMARY_REFRESH_MS).toBeGreaterThanOrEqual(10 * REFRESH_MS);
    expect(UNREAD_REFRESH_MS).toBeGreaterThan(REFRESH_MS);
    expect(dashboardSource).toContain("usePolled(winRateSummary, every(SUMMARY_REFRESH_MS), enabled)");
    expect(dashboardSource).toContain('useLiveEvents(["notifications"]');
  });

  it("Notifications: a live event and Mark read re-read only the feed", () => {
    expect(notificationsSource).toContain("useNotificationReads(");
    expect(notificationsSource).not.toContain("useLiveEvents(");
    expect(notificationsSource.match(/refreshFeed\(\)/g)?.length).toBe(2);
  });
});

describe("IST times", () => {
  it("the shared formatter, whatever the device zone", () => {
    expect(systemListWhen("2026-10-08T05:12:00Z")).toBe("8 Oct 2026, 10:42 IST");
    expect(systemListWhen(null)).toBe("never");
    expect(shortDate("2026-10-08T20:00:00Z")).toBe("9 Oct 2026");    // 01:30 IST is the next day
    expect(printTitle("DEMO", "15m", new Date("2026-10-20T19:00:00Z"))).toBe("Chart analysis - DEMO 15m - 21 Oct 2026");
    const ticket = { status: "open", first_response_at: null, first_response_due_at: "2026-10-08T05:12:00Z", first_response_late: false } as unknown as SupportTicket;
    expect(dueLine(ticket)?.text).toBe("First answer due 8 Oct 2026, 10:42 IST");
  });
});

describe("Cancelled and the Signals source filter", () => {
  it("a cancelled order is its own group, never Rejected", () => {
    expect(orderGroup("CANCELLED")).toBe("cancelled");
    expect(orderGroup("REJECTED")).toBe("rejected");
  });

  it("the signal generator has its own source filter", () => {
    expect(SOURCES.map((s) => s.id)).toContain("library");
    expect(SOURCES.find((s) => s.id === "library")?.label).toBe("Signal generator");
    expect(feedQuery({ tab: "today", source: "library" })).toBe("tab=today&source=library");
  });
});
