import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ChainHistoryView, chainHistoryState, formatBytes, gapText } from "./ChainHistoryCard";
import cardSource from "./ChainHistoryCard.tsx?raw";
import type { ChainHistoryRow, ChainHistoryStatus } from "./consoleApi";

/** TM 9.4: the admin card of the owner's chain history - states, gaps, sizes; read-only and price-free. */

const row = (over: Partial<ChainHistoryRow> = {}): ChainHistoryRow => ({
  underlying: "NIFTY", exchange: "NSE", trading_day: true, chains_today: 120, slots_today: 60, slots_expected: 63, missing_slots: 3,
  gaps: [{ from: "2026-10-23T09:16:00+05:30", to: "2026-10-23T09:18:00+05:30" }, { from: "2026-10-23T10:01:00+05:30", to: "2026-10-23T10:02:00+05:30" }],
  stored_bytes_today: 2_400_000, raw_bytes_today: 9_000_000, last_captured_at: "2026-10-23T04:48:00+00:00", ...over,
});

const status = (over: Partial<ChainHistoryStatus> = {}): ChainHistoryStatus => ({
  flag_on: true, owner_configured: true, owner_session: { broker: "upstox" }, slot_minutes: 1, expiries_per_underlying: 2,
  max_reads_per_slot: 12, stocks_enabled: false, underlyings: [row(), row({ underlying: "SENSEX", exchange: "BSE", missing_slots: 0, gaps: [] })],
  totals: { chains: 5400, days: 3, first_day: "2026-10-21", last_captured_at: "2026-10-23T04:48:00+00:00", stored_bytes: 7_000_000 },
  problem: null, checked_at: "2026-10-23T04:50:00+00:00", ...over,
});

describe("chain history card", () => {
  it("names the state plainly", () => {
    expect(chainHistoryState(status())).toEqual({ tone: "ok", label: "Recording" });
    expect(chainHistoryState(status({ flag_on: false })).label).toBe("Off");
    expect(chainHistoryState(status({ owner_configured: false })).label).toBe("Not set up");
    expect(chainHistoryState(status({ owner_session: null }))).toEqual({ tone: "warn", label: "Waiting for the owner's login" });
  });

  it("shows today's chains, slots and the gaps per underlying", () => {
    const html = renderToStaticMarkup(<ChainHistoryView data={status()} />);
    expect(html).toContain('data-chain-row="NIFTY"');
    expect(html).toContain("60 / 63");
    expect(html).toContain("3 missing · 09:16–09:18, 10:01–10:02");
    expect(html).toContain("Every 1 min");
    expect(html).toContain("5,400 chains");
    expect(gapText(row({ missing_slots: 0, gaps: [] }))).toBe("None");
    expect(gapText(row({ trading_day: false }))).toBe("No session today");
    expect(gapText(row({ slots_expected: 0, missing_slots: 0 }))).toBe("Session not started");
  });

  it("says what is wrong when nothing is recorded", () => {
    const html = renderToStaticMarkup(<ChainHistoryView data={status({ flag_on: false, problem: "Recording is off." })} />);
    expect(html).toContain("data-chain-history-problem");
    expect(html).toContain("Recording is off.");
  });

  it("sizes in plain units", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2_400_000)).toBe("2.3 MB");
  });

  it("only reads and never shows a price", () => {
    expect(cardSource).not.toMatch(/method:\s*"(POST|PUT|PATCH|DELETE)"/);
    expect(cardSource).not.toMatch(/\bltp\b|text-\[\d+px\]/);
  });
});
