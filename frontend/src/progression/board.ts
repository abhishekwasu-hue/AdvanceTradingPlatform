// N2-6: pure helpers for the positions board. The server computes every figure from the caller's own positions; this
// shapes them into lines. Facts only.
import type { TFunction } from "i18next";
import type { BoardPosition } from "../types";

export function money(n: number | null | undefined): string {
  if (n == null) return "-";
  const s = `₹${Math.abs(n).toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  return n > 0 ? `+${s}` : n < 0 ? `-${s}` : s;
}

/** "1 h 35 min" / "12 min" / "2 d 3 h". */
export function duration(minutes: number | null | undefined): string {
  if (minutes == null || minutes < 0) return "-";
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 24 * 60) return `${Math.floor(minutes / 60)} h ${minutes % 60} min`;
  return `${Math.floor(minutes / 1440)} d ${Math.floor((minutes % 1440) / 60)} h`;
}

const r2 = (n: number | null | undefined) => (n == null ? "-" : n.toFixed(2));

export function stopLine(p: BoardPosition, t: TFunction<"progression">): string {
  if (p.stop == null || p.stop_status == null) return t("board.stop.none");
  return t(`board.stop.${p.stop_status}`, { stop: p.stop.toLocaleString("en-IN"), r: r2(p.to_stop_r) });
}

export function targetLine(p: BoardPosition, t: TFunction<"progression">): string | null {
  if (p.target == null || p.target_status == null) return null;
  return t(`board.target.${p.target_status}`, { target: p.target.toLocaleString("en-IN"), r: r2(p.to_target_r) });
}

export function driftLine(p: BoardPosition, t: TFunction<"progression">): string | null {
  if (p.stop == null || p.original_stop == null || (p.stop_drift !== "widened" && p.stop_drift !== "trailed")) return null;
  return t(`board.drift.${p.stop_drift}`, { from: p.original_stop.toLocaleString("en-IN"), to: p.stop.toLocaleString("en-IN"),
                                             basis: p.risk_basis });
}

/** The exit button's text says exactly what happens (P1 §2.5). */
export function exitLabel(p: BoardPosition, t: TFunction<"progression">): string {
  if (p.exit === "one_click") return t("board.exitNow", { mode: p.mode, symbol: p.symbol, qty: p.quantity.toLocaleString("en-IN") });
  return p.exit === "broker_app" ? t("board.exitBroker") : t("board.exitAuto");
}
