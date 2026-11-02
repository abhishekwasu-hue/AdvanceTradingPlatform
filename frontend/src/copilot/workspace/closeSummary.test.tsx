import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import source from "./CloseSummaryV2.tsx?raw";
import rulesSource from "./closeSummary.ts?raw";
import workspaceSource from "./ChartWorkspaceV2.tsx?raw";
import { summaryRows, summaryTitle, type CloseSummary, type CloseSummaryApi, type ModeSummary } from "./closeSummary";
import { CloseSummaryV2 } from "./CloseSummaryV2";

/** M76 MP-13 (D-3): the close summary in the workspace - his day in numbers only. */
const M: ModeSummary = {
  trades: { entered: 4, closed: 3, gains: 2, losses: 1, net_pnl: 1250, charges: 86 },
  accepted: [{ id: "duplicate_order", label: "Same contract and side already open or waiting", count: 2 }],
  checklist: { rules: 2, trades: 4, followed: 5, not_followed: 1, not_answered: 2 },
  fill_vs_plan: { trades: 2, average_points: 0.75, largest_points: 1 },
  open: { positions: 2, unrealised_pnl: -300, at_risk_to_your_stops: 900, without_your_stop: 1, unmarked: 0 },
};
const money = (v: number) => `Rs ${v}`;
const num = (v: number) => String(v);

describe("close summary rows", () => {
  it("numbers first, coloured by sign; the rows the data lacks are left out", () => {
    const rows = summaryRows(M, money, num);
    expect(rows.map((r) => r.key)).toEqual(["trades", "net", "accepted", "checklist", "fill", "open", "unrealised", "risk"]);
    expect(rows[0].value).toBe("4 entered · 3 closed (2 gain, 1 loss)");
    expect(rows.find((r) => r.key === "net")).toMatchObject({ value: "Rs 1250 · charges Rs 86", tone: "up" });
    expect(rows.find((r) => r.key === "unrealised")?.tone).toBe("down");
    expect(rows.find((r) => r.key === "risk")?.value).toBe("Rs 900 · 1 without your stop");
    const bare = summaryRows({ ...M, accepted: [], checklist: null, fill_vs_plan: { trades: 0, average_points: null, largest_points: null },
      open: { ...M.open, positions: 0 } }, money, num);
    expect(bare.map((r) => r.key)).toEqual(["trades", "net", "open"]);     // no checklist saved: no checklist row
  });

  it("titled So far today while the exchange is open, Close summary after", () => {
    const s: CloseSummary = { as_of: "", from: "", market_open: true, modes: {}, checklist_saved: false, blocks: false };
    expect(summaryTitle(s)).toBe("So far today");
    expect(summaryTitle({ ...s, market_open: false })).toBe("Close summary");
    expect(summaryTitle(null)).toBe("So far today");
  });

  it("renders in the workspace, read-only, no call words", () => {
    const api: CloseSummaryApi = { read: () => new Promise(() => undefined) };
    expect(renderToStaticMarkup(<CloseSummaryV2 api={api} />)).toContain('data-close-summary="loading"');
    for (const s of [source, rulesSource]) {
      expect(s).not.toMatch(/\/order|placeOrder|exit-now|basket\/execute|method: "(POST|PUT|DELETE)"/);
      expect(s).not.toMatch(/\b(buy|sell|target|stop-loss)\b/i);
    }
    expect(workspaceSource).toContain("<CloseSummaryV2 />");
  });
});
