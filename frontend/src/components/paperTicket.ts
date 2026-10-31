// Z5 / P1-d: pure helpers for the plan-before-trade PAPER ticket. The server computes every number (size, risk, cap,
// gauge fraction, costs, break-even); these only turn the form into a plan and the answer into rows, text and an arc.
import type { PaperRiskStatus, TicketCostLine, TicketPlan, TicketSetup, TradePlanView } from "../types";
import { formatDateTimeIST, formatNumber } from "../ui/format";

export interface TicketForm {
  symbol: string; direction: "LONG" | "SHORT"; setup: TicketSetup; entry: string; stop_loss: string; target: string;
  time_stop: string; invalidation: string; quantity: string; checklist: Record<string, boolean>; override: boolean; override_reason: string;
}

export const EMPTY_FORM: TicketForm = {
  symbol: "", direction: "LONG", setup: "PULLBACK_AT_LEVEL", entry: "", stop_loss: "", target: "", time_stop: "", invalidation: "",
  quantity: "", checklist: {}, override: false, override_reason: "",
};

function positive(text: string): number | null {
  const n = Number(text.trim());
  return text.trim() !== "" && Number.isFinite(n) && n > 0 ? n : null;
}

/** The plan to send, or what is missing. Reading the user's typed numbers is parsing, not computing. */
export function planFromForm(form: TicketForm): { plan: TicketPlan | null; missing: string[] } {
  const entry = positive(form.entry), stop = positive(form.stop_loss), target = positive(form.target);
  const missing = [
    !form.symbol.trim() && "symbol", entry == null && "entry", stop == null && "stop", target == null && "target",
  ].filter((m): m is string => Boolean(m));
  if (missing.length || entry == null || stop == null || target == null) return { plan: null, missing };
  const quantity = positive(form.quantity);
  return {
    plan: {
      symbol: form.symbol.trim().toUpperCase(), direction: form.direction, setup: form.setup, entry, stop_loss: stop, target,
      time_stop: form.time_stop.trim() || null, invalidation: form.invalidation.trim() || null, checklist: form.checklist,
      quantity, override: form.override, override_reason: form.override ? form.override_reason.trim() || null : null,
    },
    missing: [],
  };
}

/** Indian grouping, two decimals. */
export function money(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return "-";
  return formatNumber(n, 2);
}

const COST_LABELS: [keyof TicketCostLine["items"], string][] = [
  ["brokerage", "Brokerage"], ["stt", "STT"], ["exchange", "Exchange charges"], ["sebi", "SEBI fee"], ["stamp", "Stamp duty"],
  ["gst", "GST"], ["slippage", "Slippage (estimate)"],
];

/** The cost line in a fixed order, as the server sent it. */
export function costRows(costs: TicketCostLine): { label: string; value: string }[] {
  return COST_LABELS.map(([key, label]) => ({ label, value: money(costs.items[key]) }));
}

export type GaugeTone = "ok" | "warn" | "over";

/** The risk gauge: the server's fraction of the cap -> a 180-degree arc. Above 1 the arc is full and turns to warn
 *  (an override inside its limit) or over (above what an override may reach) - the server's zone says which. */
export function gauge(fraction: number | null | undefined, zone: "ok" | "override" | "over"): { path: string; track: string; tone: GaugeTone; label: string } {
  const f = fraction == null || !Number.isFinite(fraction) ? 0 : Math.max(0, fraction);
  const shown = Math.min(1, f);
  const r = 40, cx = 50, cy = 50;
  const angle = Math.PI * (1 - shown);
  const x = cx + r * Math.cos(angle), y = cy - r * Math.sin(angle);
  const track = `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${cx + r} ${cy}`;
  const path = shown <= 0 ? "" : `M ${cx - r} ${cy} A ${r} ${r} 0 0 1 ${x.toFixed(2)} ${y.toFixed(2)}`;
  const tone: GaugeTone = zone === "ok" ? "ok" : zone === "override" ? "warn" : "over";
  return { path, track, tone, label: `${Math.round(f * 100)}% of your cap` };
}

/** "Locked until 12 Oct 00:00 IST" from the server's lockout, or null. */
export function lockoutLine(status: Pick<PaperRiskStatus, "lockout">): string | null {
  if (!status.lockout) return null;
  const until = formatDateTimeIST(new Date(status.lockout.until), { year: false });
  return `New PAPER entries locked until ${until}. Exits stay available.`;
}

/** What the ticket says after "Place": the server's status of the stored plan, in words. */
export function placedMessage(plan: Pick<TradePlanView, "status" | "trade_id" | "filled" | "proposal" | "reasons">): string {
  if (plan.status === "PLACED") {
    return `Placed (PAPER): position #${plan.trade_id}${plan.filled ? `, ${plan.filled.quantity} at ${money(plan.filled.price)}` : ""}.`;
  }
  if (plan.status === "PROPOSED" && plan.proposal) {
    return `Sent for approval: proposal #${plan.proposal.id}. Approve it to place the PAPER order at the live price.`;
  }
  return `Not placed: ${plan.reasons.join("; ") || plan.status}`;
}

/** "Takes effect on 13 Oct: daily loss 5%" for a loosening that waits for the next IST day, or null. */
export function pendingLine(status: Pick<PaperRiskStatus, "pending" | "labels">): string | null {
  if (!status.pending) return null;
  // TT-12: a value null is a limit he cleared (back to "Not set").
  const parts = Object.entries(status.pending.values).map(([k, v]) => `${status.labels[k as keyof typeof status.labels] ?? k} ${v === null ? "not set" : v}`);
  const day = new Date(`${status.pending.from}T00:00:00+05:30`).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short" });
  return `Looser limits take effect on ${day} (not during the day they protect): ${parts.join(", ")}`;
}
