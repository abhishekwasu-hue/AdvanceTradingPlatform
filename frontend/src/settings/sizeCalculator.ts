/**
 * M94 R1 (owner order M94; OPEN_QUESTIONS M94-Q9): the position-size calculator on Settings › Risk limits. Information only:
 * it places nothing and saves nothing. The size comes from the server's own risk-based sizing (POST
 * /api/risk-settings/size-calculator), the same one an entry sized by risk uses: risk per trade / stop distance, rounded
 * down to whole lots (a cash share's lot is one share). Nothing is pre-filled; the reference is a hint only.
 */
import { request } from "../api/client";
import { formatNumber } from "../ui/format";

/** M94-Q9: the reference shown as a hint, never pre-filled. */
export const RISK_HINT = "reference 1% (study: at most 2%)";

export type SizeUnit = "SHARES" | "LOTS";

/** The form as typed ("" = empty). */
export interface SizeDraft {
  account: string; riskPct: string; riskRupees: string; entry: string; stop: string; unit: SizeUnit | ""; lotSize: string;
}

export const EMPTY_SIZE_DRAFT: SizeDraft = { account: "", riskPct: "", riskRupees: "", entry: "", stop: "", unit: "", lotSize: "" };

export interface SizeResult {
  ok: true; unit: SizeUnit; lot_size: number; quantity: number; lots: number; risk_amount: number; per_share: number; per_lot: number;
  at_risk: number; note: string | null;
}

const number = (raw: string): number | null => {
  const clean = raw.replace(/[,\s₹%]/g, "");
  if (clean === "") return null;
  const v = Number(clean);
  return Number.isFinite(v) ? v : Number.NaN;
};

/** The request body, or the first problem in plain words. Zero and negative numbers are refused (the server checks too). */
export function sizeBodyOf(draft: SizeDraft): { body: Record<string, unknown> | null; problem: string | null } {
  const entry = number(draft.entry), stop = number(draft.stop);
  const account = number(draft.account), pct = number(draft.riskPct), rupees = number(draft.riskRupees);
  for (const [v, name] of [[entry, "Entry price"], [stop, "Stop price"]] as const) {
    if (v === null || Number.isNaN(v) || v <= 0) return { body: null, problem: `${name}: enter a price above 0.` };
  }
  if (entry === stop) return { body: null, problem: "The stop price is the same as the entry price: there is no stop distance to size on." };
  if (pct === null && rupees === null) return { body: null, problem: "Risk per trade: enter a % of the account or a rupee amount." };
  if (pct !== null && (Number.isNaN(pct) || pct <= 0 || pct > 100)) {
    return { body: null, problem: "Risk per trade: the % must be more than 0 and at most 100." };
  }
  if (pct !== null && (account === null || Number.isNaN(account) || account <= 0)) {
    return { body: null, problem: "Account: enter an amount above 0 to use a % risk." };
  }
  if (account !== null && (Number.isNaN(account) || account <= 0)) return { body: null, problem: "Account: enter an amount above 0, or leave it empty." };
  if (rupees !== null && (Number.isNaN(rupees) || rupees <= 0)) return { body: null, problem: "Risk per trade: the rupee amount must be above 0." };
  if (draft.unit === "") return { body: null, problem: "Size in: pick shares or F&O lots." };
  let lot: number | null = null;
  if (draft.unit === "LOTS") {
    lot = number(draft.lotSize);
    if (lot === null || Number.isNaN(lot) || lot < 1 || !Number.isInteger(lot)) {
      return { body: null, problem: "Lot size: enter the contract's lot size, a whole number from 1." };
    }
  }
  return { body: { account, risk_pct: pct, risk_rupees: rupees, entry, stop, unit: draft.unit, lot_size: lot }, problem: null };
}

const rupees = (v: number): string => `₹${formatNumber(v, 2, { trim: true })}`;

/** The result in words: the size first, then what it risks. */
export function sizeWords(r: SizeResult): { size: string; detail: string } {
  if (r.quantity <= 0) return { size: "No size", detail: r.note ?? "Your risk per trade is less than one lot at this stop." };
  const size = r.unit === "LOTS"
    ? `${formatNumber(r.lots, 0)} lot${r.lots === 1 ? "" : "s"} (${formatNumber(r.quantity, 0)} qty)`
    : `${formatNumber(r.quantity, 0)} share${r.quantity === 1 ? "" : "s"}`;
  return { size, detail: `Risks ${rupees(r.at_risk)} of your ${rupees(r.risk_amount)} per trade · ${rupees(r.per_lot)} per ${r.unit === "LOTS" ? "lot" : "share"}` };
}

export async function calculateSize(body: Record<string, unknown>, fetcher: typeof request = request): Promise<SizeResult> {
  return fetcher<SizeResult>("/risk-settings/size-calculator", { method: "POST", body: JSON.stringify(body) });
}
