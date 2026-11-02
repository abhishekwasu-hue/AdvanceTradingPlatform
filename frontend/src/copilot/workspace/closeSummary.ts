/**
 * M76 MP-13 (owner order M76 D-3; flag `copilot_workspace`): the close summary in the workspace - his own day in numbers
 * (`GET /api/charts/workspace/close-summary`, backend app/charts/workspace_summary.py). The pure rows; no advice, no
 * judgement - figures and his own checklist only.
 */
import { request } from "../../api/client";

export interface ModeSummary {
  trades: { entered: number; closed: number; gains: number; losses: number; net_pnl: number; charges: number };
  accepted: { id: string; label: string; count: number }[];
  checklist: { rules: number; trades: number; followed: number; not_followed: number; not_answered: number } | null;
  fill_vs_plan: { trades: number; average_points: number | null; largest_points: number | null };
  open: { positions: number; unrealised_pnl: number; at_risk_to_your_stops: number; without_your_stop: number; unmarked: number };
}
export interface CloseSummary {
  as_of: string; from: string; market_open: boolean; modes: Partial<Record<"PAPER" | "LIVE", ModeSummary>>; checklist_saved: boolean; blocks: false;
}

export const closeSummaryApi = { read: () => request<CloseSummary>("/charts/workspace/close-summary") };
export type CloseSummaryApi = typeof closeSummaryApi;

export type Tone = "up" | "down" | "flat";
export interface SummaryRow { key: string; label: string; value: string; tone?: Tone }

const tone = (v: number): Tone => (v > 0 ? "up" : v < 0 ? "down" : "flat");

/** One mode's rows, numbers first; a row the data lacks is left out (his checklist not saved: no checklist row). Pure. */
export function summaryRows(m: ModeSummary, money: (v: number) => string, num: (v: number) => string): SummaryRow[] {
  const t = m.trades;
  const rows: SummaryRow[] = [
    { key: "trades", label: "Trades", value: `${t.entered} entered · ${t.closed} closed (${t.gains} gain, ${t.losses} loss)` },
    { key: "net", label: "Net P&L (closed today)", value: `${money(t.net_pnl)} · charges ${money(t.charges)}`, tone: tone(t.net_pnl) },
  ];
  if (m.accepted.length) {
    rows.push({ key: "accepted", label: "Check-card lines accepted", value: m.accepted.map((a) => `${a.label} ${a.count}`).join(" · ") });
  }
  if (m.checklist) {
    const c = m.checklist;
    rows.push({ key: "checklist", label: "Your checklist", value: `${c.followed} followed · ${c.not_followed} not followed · ${c.not_answered} not answered` });
  }
  if (m.fill_vs_plan.trades > 0 && m.fill_vs_plan.average_points != null && m.fill_vs_plan.largest_points != null) {
    rows.push({ key: "fill", label: "Fill vs planned entry", value: `average ${num(m.fill_vs_plan.average_points)} pts · largest ${num(m.fill_vs_plan.largest_points)} pts (${m.fill_vs_plan.trades})` });
  }
  const o = m.open;
  rows.push({ key: "open", label: "Open now", value: `${o.positions} position${o.positions === 1 ? "" : "s"}` });
  if (o.positions > 0) {
    rows.push({ key: "unrealised", label: "Open P&L", value: money(o.unrealised_pnl), tone: tone(o.unrealised_pnl) });
    rows.push({ key: "risk", label: "At risk to your stops", value: `${money(o.at_risk_to_your_stops)}${o.without_your_stop ? ` · ${o.without_your_stop} without your stop` : ""}` });
  }
  return rows;
}

/** The card's title: "So far today" while the exchange is open, "Close summary" after. Pure. */
export function summaryTitle(s: CloseSummary | null): string {
  return s && !s.market_open ? "Close summary" : "So far today";
}
