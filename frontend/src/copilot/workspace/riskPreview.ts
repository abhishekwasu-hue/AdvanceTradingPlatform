/**
 * M76 MP-4 (owner order M76 W-3; flag `copilot_workspace`): the trade plan's risk preview - figures only, never a
 * warning. Every number is read, not made here: max loss / profit, break-evens, the plan's Greeks, its margin estimate and
 * the IV row come from the Options Builder's engine (`builderApi.evaluate`) for the same legs; his book's Greeks from
 * `GET /api/positions/greeks?mine=true&underlying=`; his PAPER balance from `GET /api/paper/fno/risk-budget` (the same
 * balance the ticket's margin line uses). This file only adds them up and turns them into rows. Where a figure is not
 * known the row says so; nothing is assumed.
 *
 * Points: a rupee figure divided by the plan's units, only when every leg has the same units (lots x lot size) - with
 * different sizes there is no one points figure, so none is shown. Break-evens: the level and its distance from the
 * spot he typed. IV row (M76Q-5): only when he saved `warnings.iv_rise_pct`; the change in the plan's value today at his
 * spot, from the builder's model with IV raised by his points; margin under that IV is not known (no SPAN in PAPER).
 */
import { formatNumber } from "../../ui/format";
import { money, seriesAt, type Evaluation, type Leg } from "../../optionsBuilder/model";

export const IV_RISE_KEY = "warnings.iv_rise_pct";
export const NOT_KNOWN = "Not known";

/** The book's sums as the Greeks route returns them (`net`, `underlying_net`). */
export interface BookGreeks { net_delta: number; net_theta: number; net_vega: number; legs?: number }
export interface BookRead { net: BookGreeks | null; underlying: (BookGreeks & { legs: number }) | null; problem: string | null }

export interface RiskRow {
  key: string; label: string; value: string; sub?: string;
  tone?: "up" | "down" | "fg";
}

export interface RiskInputs {
  evaluation: Evaluation; legs: Leg[]; spot: number;
  balance: number | null;                      // his PAPER balance (null = not known)
  book: BookRead | null;                       // null while it is read
  ivPoints: number | null;                     // his saved IV rise (null = Not set: no IV row)
  ivEvaluation: Evaluation | null;             // the builder's reply with IV raised by his points
}

/** The plan's units when every leg has the same size, else null (no single points figure). */
export function uniformUnits(legs: Leg[]): number | null {
  if (!legs.length) return null;
  const units = legs.map((l) => l.lots * l.lot_size);
  return units.every((u) => u === units[0]) && units[0] > 0 ? units[0] : null;
}

/** A signed figure with two decimals ("+12.50", "−3.25"; zero unsigned). */
function formatSigned(v: number, decimals: number): string {
  const body = formatNumber(v, decimals);
  return v > 0 && /[1-9]/.test(body) ? `+${body}` : body;
}

const pts = (v: number) => `${formatNumber(Math.round(Math.abs(v) * 100) / 100, 2)} points`;
const signedPts = (v: number) => `${formatSigned(Math.round(v * 100) / 100, 2)} points`;
const greek = (v: number) => formatSigned(Math.round(v * 100) / 100, 2);

/** The rows, in a fixed order; a row whose source is missing says "Not known" (never a made-up figure). */
export function riskRows(i: RiskInputs): RiskRow[] {
  const e = i.evaluation;
  const units = uniformUnits(i.legs);
  const rows: RiskRow[] = [];
  const ext = e.extremes;
  const extreme = (key: string, label: string, value: number | null | undefined, unbounded: boolean | undefined, tone: "up" | "down") => {
    if (unbounded) { rows.push({ key, label, value: "Unlimited", tone }); return; }
    if (value == null || !Number.isFinite(value)) { rows.push({ key, label, value: NOT_KNOWN }); return; }
    rows.push({ key, label, value: money(value), sub: units ? pts(value / units) : undefined, tone });
  };
  extreme("max_loss", "Max loss", ext?.max_loss, ext?.unbounded_loss, "down");
  extreme("max_profit", "Max profit", ext?.max_profit, ext?.unbounded_profit, "up");
  const bes = e.breakevens ?? [];
  rows.push({
    key: "breakevens", label: bes.length > 1 ? "Break-evens" : "Break-even",
    value: bes.length ? bes.map((b) => formatNumber(b, 2)).join(" · ") : "None",
    sub: bes.length ? bes.map((b) => `${signedPts(b - i.spot)} from spot`).join(" · ") : undefined,
  });

  const margin = e.margin?.value;
  if (typeof margin === "number" && Number.isFinite(margin) && i.balance != null) {
    const after = i.balance - margin;
    rows.push({ key: "margin_after", label: "Balance after this margin", value: money(after), tone: after < 0 ? "down" : "fg",
      sub: `PAPER balance ${money(i.balance)} · margin estimate ${money(margin)}` });
  } else {
    rows.push({ key: "margin_after", label: "Balance after this margin", value: NOT_KNOWN,
      sub: i.balance == null ? "Your PAPER balance is not known." : "Only the broker can say this plan's margin." });
  }

  const g = e.summary.greeks;
  const book = i.book;
  if (!book) {
    rows.push({ key: "book", label: "Your book after this trade", value: "Reading…" });
  } else if (!book.net) {
    rows.push({ key: "book", label: "Your book after this trade", value: NOT_KNOWN, sub: book.problem ?? undefined });
  } else {
    const n = book.net;
    rows.push({ key: "book", label: "Your book after this trade",
      value: `Δ ${greek(n.net_delta + g.delta)} · Θ ${greek(n.net_theta + g.theta)} · V ${greek(n.net_vega + g.vega)}`,
      sub: `Now Δ ${greek(n.net_delta)} · Θ ${greek(n.net_theta)} · V ${greek(n.net_vega)}` });
    const u = book.underlying;
    if (u) {
      rows.push({ key: "same_underlying", label: "Same underlying after this trade",
        value: `Δ ${greek(u.net_delta + g.delta)} · Θ ${greek(u.net_theta + g.theta)} · V ${greek(u.net_vega + g.vega)}`,
        sub: `${u.legs} open option leg${u.legs === 1 ? "" : "s"} now · Δ ${greek(u.net_delta)}` });
    }
  }

  if (i.ivPoints != null) {
    const base = seriesAt(e.prices, e.today, i.spot);
    const shifted = i.ivEvaluation ? seriesAt(i.ivEvaluation.prices, i.ivEvaluation.today, i.spot) : null;
    const label = `IV +${formatNumber(i.ivPoints, 2)} points (model)`;
    if (base == null || shifted == null) rows.push({ key: "iv", label, value: i.ivEvaluation ? NOT_KNOWN : "Reading…", sub: "Margin under this IV: not known." });
    else {
      const d = shifted - base;
      rows.push({ key: "iv", label, value: `${d >= 0 ? "+" : "−"}${money(Math.abs(d))} today`, tone: d >= 0 ? "up" : "down",
        sub: "Margin under this IV: not known." });
    }
  }
  return rows;
}

/** His saved IV rise (IV points), else null - the row does not exist until he saves one. */
export function ivPointsOf(fields: { key: string; value: unknown; saved: boolean }[] | undefined): number | null {
  const v = fields?.find((f) => f.key === IV_RISE_KEY && f.saved)?.value;
  return typeof v === "number" && Number.isFinite(v) && v > 0 ? v : null;
}
