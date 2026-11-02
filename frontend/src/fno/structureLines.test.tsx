import { afterEach, describe, expect, it } from "vitest";
import { openChart, setOpenChart } from "../chart/openChart";
import workspaceSource from "../copilot/workspace/ChartWorkspaceV2.tsx?raw";
import { assembleLines, switchesOf, MISTAKES, WARNING_IDS } from "./checkCard";
import { checkDraftOf } from "./OrderTicketV2";
import { newDraft, type TicketTarget } from "./ticketModel";

/**
 * M76 MP-3: the structure lines in the ONE check card - their own switches, after the mistakes and the facts (worst
 * first), never a mistake, and the workspace's open chart sent with the draft (M76Q-7) so the server reads that
 * timeframe; no chart open: nothing extra is sent (the server reads his signal timeframe and the daily).
 */
const STRUCTURE = ["counter_trend", "trend_conflict", "area_conflict", "strike_in_area", "room"] as const;
const TARGET: TicketTarget = { underlying: "NIFTY", expiry: "2026-11-03", strike: 24500, right: "CE", label: null, lotSize: 75, ltp: 120, bid: null, ask: null };

afterEach(() => setOpenChart(null));

describe("M76 MP-3: structure lines in the card", () => {
  it("each structure type has its own switch, ON as information, and is never a mistake", () => {
    const on = switchesOf(null);
    for (const id of STRUCTURE) {
      expect(WARNING_IDS).toContain(id);
      expect(MISTAKES).not.toContain(id);
      expect(on[id]).toEqual({ on: true, ask: false });
    }
  });

  it("worst first: the structure lines come after the mistakes and the facts; a switch off removes its line", () => {
    const server = [
      { id: "room", severity: "fact" as const, text: "Next seller area (daily, 24600.00 - 24680.00): 62.00 points above the last close 24538.00 · ATP chart readers" },
      { id: "trend_conflict", severity: "fact" as const, text: "Daily trend: down · your setup: bullish · ATP chart readers" },
      { id: "market_state", severity: "fact" as const, text: "Market: pre-open (09:00-09:15)" },
      { id: "duplicate_order", severity: "mistake" as const, text: "Open: NIFTY 03 NOV 24500 CE buy 1 lot · this order: buy 1 lot" },
    ];
    const lines = assembleLines({ server, switches: switchesOf(null) });
    expect(lines.map((l) => l.id)).toEqual(["duplicate_order", "market_state", "trend_conflict", "room"]);
    const off = { ...switchesOf(null), room: { on: false, ask: false } };
    expect(assembleLines({ server, switches: off }).map((l) => l.id)).toEqual(["duplicate_order", "market_state", "trend_conflict"]);
  });

  it("the draft carries the workspace's open chart only while one is open", () => {
    expect(checkDraftOf(TARGET, { ...newDraft("BUY"), lots: 1 })).not.toHaveProperty("chart_tf");
    setOpenChart({ instrument: "NSE_INDEX:NIFTY50", tf: "15m" });
    expect(checkDraftOf(TARGET, { ...newDraft("BUY"), lots: 1 })).toMatchObject({ chart_instrument: "NSE_INDEX:NIFTY50", chart_tf: "15m" });
    setOpenChart(null);
    expect(openChart()).toBeNull();
  });

  it("the workspace publishes its open chart and clears it when it closes; it still opens no ticket", () => {
    expect(workspaceSource).toMatch(/setOpenChart\(openInstrument && openTf/);
    expect(workspaceSource).toMatch(/return \(\) => setOpenChart\(null\)/);
    expect(workspaceSource).not.toMatch(/placeOrder|\/order|basket|ticket/i);
  });
});
