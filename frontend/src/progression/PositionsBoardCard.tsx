import { useEffect, useState } from "react";
import { api } from "../api/client";
import { Card } from "../components/ui";
import type { BoardPosition, PositionsBoard } from "../types";
import { driftLine, duration, exitLabel, money, stopLine, targetLine } from "./board";
import { useProgressionT } from "./i18n";

/**
 * N2-6 (flag `positions_board`): the caller's own open positions with P&L in ₹ and R, stop and target status, time in
 * trade, plan vs current stop (a widened stop is highlighted) and the exit. The exit is the existing "Exit now": ONE
 * click, no dialog, never disabled by a lockout or a pause (ADR-0004); its text says exactly what happens. Hidden while
 * the flag is off (the API answers 404). `exitKey` (the Trade workspace only) makes rows focusable with "x" as Exit now;
 * elsewhere, such as the Positions page, the button is the only way. The flag never gates the exit itself.
 */
export default function PositionsBoardCard({ onChanged, exitKey = false }: { onChanged?: () => void; exitKey?: boolean }) {
  const [board, setBoard] = useState<PositionsBoard | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [exiting, setExiting] = useState<number | null>(null);
  const t = useProgressionT();
  const load = () => { api.positionsBoard().then(setBoard).catch(() => setBoard(null)); };
  useEffect(load, []);
  if (!board) return null;

  async function exit(p: BoardPosition) {
    setExiting(p.trade_id); setMessage(null);
    try {
      const out = await api.exitNow(p.trade_id);
      const first = out.closed[0];
      setMessage(t("board.exited", { symbol: p.symbol, price: first?.exit_price ?? "-" }));
      load(); onChanged?.();
    } catch (e) { setMessage(String(e)); } finally { setExiting(null); }
  }
  return <PositionsBoardView board={board} exiting={exiting} message={message} onExit={(p) => void exit(p)} exitKey={exitKey} />;
}

export function PositionsBoardView({ board, exiting, message, onExit, exitKey = false }: {
  board: PositionsBoard; exiting: number | null; message: string | null; onExit: (p: BoardPosition) => void; exitKey?: boolean;
}) {
  const t = useProgressionT();
  const tot = board.totals;
  return (
    <Card title={t("board.title")} className="space-y-3">
      <div className="space-y-3 text-xs" data-testid="positions-board">
        {board.lockout && <p className="rounded bg-warn/15 px-2 py-1 text-warn" role="status">{t("board.lockout", { reason: board.lockout.reason })}</p>}
        {!board.positions.length ? <p className="text-fg-muted">{t("board.none")}</p> : (
          <>
            <p className="text-fg">{t("board.totals", { count: tot.positions, pnl: money(tot.pnl), r: (tot.r ?? 0).toFixed(2), risk: money(tot.to_stops_from_marks).replace("+", "") })}</p>
            {(tot.widened_stops > 0 || tot.near_stop > 0 || tot.unmarked > 0) && (
              <p className="text-fg-muted">{t("board.flags", { widened: tot.widened_stops, near: tot.near_stop, unmarked: tot.unmarked })}</p>
            )}
            <ul className="space-y-2">
              {board.positions.map((p) => {
                const drift = driftLine(p, t);
                const target = targetLine(p, t);
                const tone = (p.pnl ?? 0) > 0 ? "text-up" : (p.pnl ?? 0) < 0 ? "text-down" : "text-fg";
                return (
                  // N2-9: in the Trade workspace (exitKey) a row takes focus and "x" on it is Exit now (one key, the same
                  // call as the button); elsewhere the row is not focusable and "x" does nothing
                  <li key={p.trade_id} tabIndex={exitKey ? 0 : undefined}
                      className={`rounded-lg border p-2 ${exitKey ? "outline-none focus:ring-2 focus:ring-info " : ""}${p.stop_drift === "widened" ? "border-warn" : "border-border"}`}
                      data-testid={`board-${p.trade_id}`} data-drift={p.stop_drift ?? ""}
                      onKeyDown={exitKey ? (e) => { if (e.key === "x" && !e.ctrlKey && !e.metaKey && !e.altKey && p.exit === "one_click" && exiting !== p.trade_id) { e.preventDefault(); e.stopPropagation(); onExit(p); } } : undefined}>
                    <div className="flex flex-wrap items-baseline justify-between gap-2">
                      <span className="font-semibold text-fg">{p.direction} {p.symbol} ×{p.quantity.toLocaleString("en-IN")} · {p.mode}</span>
                      <span className={`tabular-nums ${tone}`}>
                        {p.r_multiple == null ? `${money(p.pnl)} · ${t("board.noR")}` : t("board.pnl", { pnl: money(p.pnl), r: p.r_multiple.toFixed(2) })}
                      </span>
                    </div>
                    <p className="text-fg-muted">
                      {t("board.inTrade", { time: duration(p.time_in_trade_minutes) })} ·{" "}
                      {p.price_basis === "mark" ? t("board.markAge", { age: p.mark_age_minutes ?? 0 }) : t("board.atEntry")}
                    </p>
                    <p className={p.stop_status === "near" || p.stop_status === "through" ? "text-warn" : "text-fg"}>{stopLine(p, t)}</p>
                    {target && <p className={p.target_status === "reached" ? "text-up" : "text-fg"}>{target}</p>}
                    {drift && <p className={p.stop_drift === "widened" ? "font-medium text-warn" : "text-fg-muted"}>{drift}</p>}
                    {p.plan && <p className="text-fg-muted">{t("board.plan", { setup: p.plan.setup, entry: p.plan.entry, stop: p.plan.stop_loss, target: p.plan.target })}</p>}
                    <div className="mt-1">
                      {p.exit === "one_click" ? (
                        <button type="button" disabled={exiting === p.trade_id} onClick={() => onExit(p)}
                                className="min-h-[44px] rounded border border-down px-2 py-1 text-down hover:bg-down/10 disabled:opacity-50 sm:min-h-0">
                          {exitLabel(p, t)}
                        </button>
                      ) : <span className="text-fg-muted">{exitLabel(p, t)}</span>}
                    </div>
                  </li>
                );
              })}
            </ul>
          </>
        )}
        {message && <p role="status" className="text-fg-muted">{message}</p>}
        <p className="text-[11px] text-fg-muted">{board.exits} {board.note}</p>
      </div>
    </Card>
  );
}
