import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { withAccepted, type CheckLine } from "../fno/checkCard";
import ticketSource from "../fno/OrderTicketV2.tsx?raw";
import basketSource from "../fno/BasketPanelV2.tsx?raw";
import { DEMO_WEEK } from "./journal.fixture";
import { WeeklyReviewV2 } from "./WeeklyReviewV2";
import type { AcceptedWarningRow } from "./types";

/** M76 MP-5 (W-4): the lines shown at Place go with the order; the weekly review counts them with outcomes, numbers only. */
const line = (id: CheckLine["id"], text: string): CheckLine => ({ id, text, severity: "mistake", ask: false });
const ROWS: AcceptedWarningRow[] = [
  { id: "margin_short", label: "Margin more than available", trades: 3, closed: 2, wins: 1, losses: 1, net_pnl: 200 },
  { id: "fo_ban", label: "F&O ban list", trades: 1, closed: 0, wins: 0, losses: 0, net_pnl: 0 },
];
const view = (acceptedOn: boolean, rows = ROWS) => renderToStaticMarkup(
  <WeeklyReviewV2 state={{ status: "ok", data: { ...DEMO_WEEK, accepted_warnings: rows } }} week="2032-W11" onWeek={() => undefined}
    onRetry={() => undefined} onOpen={() => undefined} acceptedOn={acceptedOn} />);
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ");

describe("M76 MP-5 accepted lines", () => {
  it("the order body carries each shown type once; no lines = the body exactly as before", () => {
    const body = { lots: 1 };
    expect(withAccepted(body, [])).toBe(body);
    expect(withAccepted(body, [line("duplicate_order", "a"), line("margin_short", "b"), line("duplicate_order", "c")]))
      .toEqual({ lots: 1, accepted_warnings: ["duplicate_order", "margin_short"] });
  });

  it("the ticket and the basket send them only with the card on", () => {
    expect(ticketSource).toContain('withAccepted(askOf(target, draft, ref.current), warnOn && step.kind === "review" ? lines : [])');
    expect(basketSource).toMatch(/withAccepted\(\{ \.\.\.basketAsk[\s\S]*?warnOn \? lines : \[\]\)/);
  });

  it("the weekly card: counts, won / lost and the net of the closed ones; hidden while the flag is off or with none", () => {
    const t = text(view(true));
    expect(t).toContain("Warnings you accepted");
    expect(t).toContain("Margin more than available");
    expect(t).toContain("None closed");
    expect((view(true).match(/data-accepted=/g) ?? []).length).toBe(2);
    expect(view(false)).not.toContain("data-accepted");
    expect(view(true, [])).not.toContain("Warnings you accepted");
    expect(t).not.toMatch(/\b(should|avoid|risky|consider|don't)\b/i);
  });
});
