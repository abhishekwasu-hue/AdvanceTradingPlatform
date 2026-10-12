import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ShortcutHelp, WorkspaceStatus } from "../pages/TradeWorkspacePage";
import { pageFromPath, pathFor } from "../routes";
import type { BoardPosition, PositionsBoard, WorkerStatus } from "../types";
import { PositionsBoardView } from "./PositionsBoardCard";
import { DEFAULT_LAYOUT, LAYOUT_KEY, flattenTargets, loadLayout, saveLayout, shortcutFor, staleness } from "./workspace";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");

function memory(initial: Record<string, string> = {}) {
  const data = { ...initial };
  return { getItem: (k: string) => data[k] ?? null, setItem: (k: string, v: string) => { data[k] = v; }, data };
}
const key = (k: string, extra: Partial<Parameters<typeof shortcutFor>[0]> = {}) =>
  shortcutFor({ key: k, shiftKey: false, ctrlKey: false, metaKey: false, altKey: false, targetTag: "BODY", ...extra });

const pos = (id: number, exit: BoardPosition["exit"]): BoardPosition => ({
  trade_id: id, symbol: `S${id}`, mode: exit === "broker_app" ? "LIVE" : "PAPER", direction: "LONG", quantity: 10, instrument_kind: "UNDERLYING",
  underlying: null, leg_group_id: null, entry_price: 100, entry_time: null, time_in_trade_minutes: 5, mark_price: 101, mark_time: null,
  mark_age_minutes: 1, price_basis: "mark", pnl: 10, r_multiple: 0.25, initial_risk: 40, risk_basis: "initial stop", stop: 96, stop_status: "ok",
  to_stop_r: 1.25, target: null, target_status: null, to_target_r: null, plan: null, original_stop: 96, stop_drift: "unchanged", exit,
});
const board = (positions: BoardPosition[]): PositionsBoard => ({
  positions, totals: { positions: positions.length, pnl: 0, r: 0, initial_risk: 0, to_stops_from_marks: 0, without_stop: 0, widened_stops: 0, near_stop: 0, unmarked: 0 },
  lockout: null, exits: "Exits are never locked.", near_r: 0.25, as_of: "", note: "",
});

describe("workspace helpers (N2-9)", () => {
  it("keeps the layout per viewer and survives bad or missing storage", () => {
    const s = memory();
    expect(loadLayout(s)).toEqual(DEFAULT_LAYOUT);
    saveLayout({ symbol: "RELIANCE", timeframe: "15min", panels: { chart: true, ticket: false, positions: true } }, s);
    expect(loadLayout(s)).toEqual({ symbol: "RELIANCE", timeframe: "15min", panels: { chart: true, ticket: false, positions: true } });
    expect(loadLayout(memory({ [LAYOUT_KEY]: "{not json" }))).toEqual(DEFAULT_LAYOUT);
    expect(loadLayout(memory({ [LAYOUT_KEY]: JSON.stringify({ timeframe: "7min", symbol: 5 }) }))).toEqual(DEFAULT_LAYOUT);
    expect(loadLayout(null)).toEqual(DEFAULT_LAYOUT);
    const throwing = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
    expect(loadLayout(throwing)).toEqual(DEFAULT_LAYOUT);
    expect(() => saveLayout(DEFAULT_LAYOUT, throwing)).not.toThrow();
  });

  it("maps keys to actions, never while typing or with a modifier", () => {
    expect(key("t")).toBe("ticket");
    expect(key("c")).toBe("chart");
    expect(key("x")).toBe("exit");
    expect(key("?")).toBe("help");
    expect(key("F", { shiftKey: true })).toBe("flatten");
    expect(key("f")).toBeNull();                                        // flatten needs Shift
    expect(key("t", { targetTag: "INPUT" })).toBeNull();
    expect(key("x", { editable: true })).toBeNull();
    expect(key("t", { ctrlKey: true })).toBeNull();
    expect(key("T", { shiftKey: true })).toBeNull();
  });

  it("flattens only the positions Exit now can close here", () => {
    expect(flattenTargets(board([pos(1, "one_click"), pos(2, "broker_app"), pos(3, "automatic"), pos(4, "one_click")])).map((p) => p.trade_id)).toEqual([1, 4]);
    expect(flattenTargets(null)).toEqual([]);
  });

  it("calls intraday data stale only while the market is open and three bars late", () => {
    const now = new Date("2026-10-12T06:00:00Z");
    expect(staleness("2026-10-12T05:50:00Z", "5min", now, true)).toEqual({ minutes: 10, stale: false });
    expect(staleness("2026-10-12T05:40:00Z", "5min", now, true)).toEqual({ minutes: 20, stale: true });
    expect(staleness("2026-10-12T05:40:00Z", "5min", now, false).stale).toBe(false);
    expect(staleness("2026-10-09T10:00:00Z", "day", now, true).stale).toBe(false);
    expect(staleness(null, "5min", now, true)).toEqual({ minutes: null, stale: false });
  });

  it("has its own address, outside the sidebar", () => {
    expect(pageFromPath(pathFor("trade"))).toBe("trade");
  });
});

describe("workspace views (N2-9)", () => {
  const market = (open: boolean): WorkerStatus => ({ worker_name: "w", running: true, healthy: true, last_seen_at: null, seconds_since_heartbeat: 1, cycle_count: 1,
                                                     last_cycle_ms: 1, last_error: null, cycle_seconds: 60, market_open: open,
                                                     market_status: open ? "OPEN" : "CLOSED", next_market_open: open ? null : "2026-10-13T03:45:00Z" });
  it("shows the market state, the lockout as a fact and stale data", () => {
    const words = text(renderToStaticMarkup(<WorkspaceStatus market={market(false)} lockout="Daily loss limit reached" staleMinutes={20} />));
    expect(words).toContain("Market: CLOSED · next open");
    expect(words).toContain("New entries are locked today: Daily loss limit reached. Exits still work.");
    expect(words).toContain("Chart data is stale: the last bar is 20 min old");
    expect(text(renderToStaticMarkup(<WorkspaceStatus market={null} lockout={null} staleMinutes={null} />))).toContain("Market state unknown");
  });

  it("lists the shortcuts and makes position rows focusable", () => {
    expect(text(renderToStaticMarkup(<ShortcutHelp />))).toContain("Shift+F - flatten all: exit every position you can close here, after one confirmation");
    const html = renderToStaticMarkup(<PositionsBoardView board={board([pos(1, "one_click")])} exiting={null} message={null} onExit={() => undefined} />);
    expect(html).toContain('tabindex="0"');
  });
});
