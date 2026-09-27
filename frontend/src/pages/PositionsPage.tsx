import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import ContractNoteCard from "../components/ContractNoteCard";
import { Card, DirectionBadge, StatTile } from "../components/ui";
import type { PositionGreeks, TradeRecord } from "../types";

export default function PositionsPage() {
  const { user, loading: authLoading } = useAuth();
  const [positions, setPositions] = useState<TradeRecord[]>([]);
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [priceInputs, setPriceInputs] = useState<Record<number, string>>({});
  const [markMessages, setMarkMessages] = useState<Record<number, string>>({});
  const [greeks, setGreeks] = useState<PositionGreeks | null>(null);
  const [greeksError, setGreeksError] = useState<string | null>(null);
  const [greeksBusy, setGreeksBusy] = useState(false);

  async function loadGreeks() {
    setGreeksBusy(true); setGreeksError(null);
    try {
      setGreeks(await api.positionGreeks());
    } catch (e) {
      setGreeksError(String(e));
    } finally {
      setGreeksBusy(false);
    }
  }

  function refresh() {
    setLoading(true);
    return Promise.all([api.listPositions(), api.listTrades()])
      .then(([p, t]) => {
        setPositions(p);
        setTrades(t);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    if (!user) return;
    refresh();
  }, [user]);

  async function handleMarkPrice(tradeId: number) {
    const raw = priceInputs[tradeId];
    const price = Number(raw);
    if (!raw || Number.isNaN(price)) return;
    try {
      const result = await api.markPrice(tradeId, price);
      setMarkMessages((m) => ({
        ...m,
        [tradeId]: result.closed
          ? `Closed: ${result.exit_reason} @ ${result.exit_price} (P&L ${result.pnl?.toFixed(2)})`
          : "Price doesn't hit SL/target yet - still open.",
      }));
      if (result.closed) refresh();
    } catch (e) {
      setMarkMessages((m) => ({ ...m, [tradeId]: String(e) }));
    }
  }

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-extrabold text-emerald-400">Positions</h1>
        <Card>
          <p className="text-sm text-muted">
            Log in from the Account tab to see your paper-execute positions and trade history.
            Anonymous paper-execute calls (from the Signals tab without signing in) aren't saved
            anywhere - they're a try-it-without-an-account demo only.
          </p>
        </Card>
      </div>
    );
  }

  const closedTrades = trades.filter((t) => t.exit_time !== null);

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-emerald-400">Positions</h1>
        <p className="text-sm font-semibold text-emerald-400/60">Paper trades executed from the Signals tab while signed in as {user.email}.</p>
      </div>

      {error && <div className="text-sm text-danger">{error}</div>}
      {loading && <div className="text-sm text-muted">Loading…</div>}

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        <StatTile label="Open Positions" value={positions.length} />
        <StatTile label="Total Trades" value={trades.length} />
        <StatTile label="Closed Trades" value={closedTrades.length} />
      </div>

      {positions.some((p) => p.instrument_kind === "OPTION") && (
        <Card title="Option Greeks (open positions)">
          <div className="flex flex-wrap items-center gap-3 text-xs">
            <button onClick={loadGreeks} disabled={greeksBusy} className="rounded border border-border hover:bg-panel2 px-3 py-1 text-slate-200 disabled:opacity-50">
              {greeksBusy ? "Computing…" : "Compute from live premiums"}
            </button>
            <span className="text-muted">IV solved from each leg's last price and the underlying's spot (Black-Scholes); short legs carry inverted position Greeks.</span>
          </div>
          {greeksError && <div className="mt-2 text-xs text-danger">{greeksError}</div>}
          {greeks && (
            <div className="mt-3 space-y-2 text-xs">
              <div className="flex flex-wrap gap-4 font-semibold text-slate-200">
                <span>Net Δ {greeks.net.net_delta}</span><span>Γ {greeks.net.net_gamma}</span><span>Θ {greeks.net.net_theta}/day</span><span>V {greeks.net.net_vega}</span>
                <span className="text-muted font-normal">as of {new Date(greeks.as_of).toLocaleTimeString()}</span>
              </div>
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead className="text-muted uppercase text-[10px] tracking-wide">
                    <tr className="text-left"><th className="py-1 pr-3">Leg</th><th className="py-1 pr-3">Qty</th><th className="py-1 pr-3">Premium</th><th className="py-1 pr-3">IV</th><th className="py-1 pr-3">Δ</th><th className="py-1 pr-3">Γ</th><th className="py-1 pr-3">Θ</th><th className="py-1 pr-3">V</th><th className="py-1 pr-3">Position Δ</th></tr>
                  </thead>
                  <tbody>
                    {greeks.legs.map((l) => (
                      <tr key={l.trade_id} className="border-t border-border">
                        <td className="py-1 pr-3 text-slate-200">{l.symbol}{l.leg_role ? <span className="text-muted"> · {l.leg_role.toLowerCase()}</span> : null}</td>
                        <td className="py-1 pr-3">{l.quantity}</td><td className="py-1 pr-3">{l.premium}</td>
                        <td className="py-1 pr-3">{(l.implied_volatility * 100).toFixed(1)}%</td>
                        <td className="py-1 pr-3">{l.delta}</td><td className="py-1 pr-3">{l.gamma}</td><td className="py-1 pr-3">{l.theta}</td><td className="py-1 pr-3">{l.vega}</td>
                        <td className="py-1 pr-3 font-semibold">{l.position_delta}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {greeks.skipped.length > 0 && <div className="text-muted">Skipped: {greeks.skipped.map((k) => `#${k.trade_id} ${k.reason}`).join("; ")}</div>}
            </div>
          )}
        </Card>
      )}

      <Card title="Open positions">
        {positions.length === 0 ? (
          <div className="text-sm text-muted py-4 text-center">No open positions yet - execute a signal from the Signals tab.</div>
        ) : (
          <div className="space-y-2">
            {positions.map((p) => (
              <div key={p.id} className="rounded border border-border px-3 py-2 text-sm space-y-1.5">
                <div className="flex flex-wrap items-center gap-3">
                  <span className="font-medium text-slate-200">{p.symbol}</span>
                  <DirectionBadge direction={p.direction as "LONG" | "SHORT"} />
                  {p.leg_group_id && (
                    <span className="rounded border border-purple-500/40 bg-purple-500/10 px-1.5 py-0.5 text-[10px] font-semibold text-purple-300" title={`group ${p.leg_group_id}`}>
                      {(p.option_strategy ?? "STRUCTURE").replace(/_/g, " ").toLowerCase()} · {p.leg_role?.toLowerCase()} leg
                      {p.group_meta ? ` · credit ${p.group_meta.net_credit} · max loss ${p.group_meta.max_loss}/unit · exit ≤${p.group_meta.target_value} / ≥${p.group_meta.stop_value}` : ""}
                    </span>
                  )}
                  <span className="text-muted text-xs">{p.strategy_id}</span>
                  <span className="text-xs text-muted">Entry {p.entry_price.toFixed(2)} × {p.quantity}</span>
                  {p.instrument_kind && p.instrument_kind !== "UNDERLYING" && p.underlying_symbol ? (
                    <span className="text-xs text-muted">
                      on {p.underlying_direction} {p.underlying_symbol}: SL {p.underlying_stop_loss?.toFixed(2) ?? "-"} / T1 {p.underlying_target1?.toFixed(2) ?? "-"}
                      {p.instrument_kind === "OPTION" ? ` · premium ${p.option_position === "WRITE" ? "ceiling" : "floor"} ${p.stop_loss.toFixed(2)}` : ` · stop ${p.stop_loss.toFixed(2)}`}
                    </span>
                  ) : (
                    <>
                      <span className="text-xs text-danger">SL {p.stop_loss.toFixed(2)}</span>
                      <span className="text-xs text-accent">T1 {p.target1?.toFixed(2) ?? "-"}</span>
                    </>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-xs text-muted">No live price feed yet - check manually:</span>
                  <input
                    type="number"
                    placeholder="Current price"
                    className="w-32 rounded bg-panel2 border border-border px-2 py-1 text-xs"
                    value={priceInputs[p.id] ?? ""}
                    onChange={(e) => setPriceInputs((v) => ({ ...v, [p.id]: e.target.value }))}
                  />
                  <button
                    onClick={() => handleMarkPrice(p.id)}
                    className="rounded border border-border hover:bg-panel2 px-2 py-1 text-xs text-slate-200"
                  >
                    Check price
                  </button>
                  {markMessages[p.id] && <span className="text-xs text-muted">{markMessages[p.id]}</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </Card>

      <Card title="Trade history">
        <TradeTable rows={trades} emptyMessage="No trades yet." />
      </Card>

      {user.role !== "VIEWER" && <ContractNoteCard onApplied={() => { refresh(); }} />}
    </div>
  );
}

function TradeTable({ rows, emptyMessage }: { rows: TradeRecord[]; emptyMessage: string }) {
  if (rows.length === 0) {
    return <div className="text-sm text-muted py-4 text-center">{emptyMessage}</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead className="text-muted uppercase text-[10px] tracking-wide">
          <tr className="text-left">
            <th className="py-1 pr-3">Symbol</th>
            <th className="py-1 pr-3">Strategy</th>
            <th className="py-1 pr-3">Direction</th>
            <th className="py-1 pr-3">Entry</th>
            <th className="py-1 pr-3">Qty</th>
            <th className="py-1 pr-3">SL</th>
            <th className="py-1 pr-3">Target 1</th>
            <th className="py-1 pr-3">Exit</th>
            <th className="py-1 pr-3">P&amp;L</th>
            <th className="py-1 pr-3">Charges</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-border">
              <td className="py-1 pr-3 font-medium text-slate-200">{r.symbol}{r.instrument_kind && r.instrument_kind !== "UNDERLYING" && <div className="text-[10px] text-muted">{r.option_position === "WRITE" ? "written" : r.instrument_kind.toLowerCase()} · lot {r.lot_size ?? "?"} · on {r.underlying_symbol}</div>}</td>
              <td className="py-1 pr-3 text-muted">{r.strategy_id}</td>
              <td className="py-1 pr-3">
                <DirectionBadge direction={r.direction as "LONG" | "SHORT"} />
              </td>
              <td className="py-1 pr-3">{r.entry_price.toFixed(2)}</td>
              <td className="py-1 pr-3">{r.quantity}</td>
              <td className="py-1 pr-3 text-danger">{r.stop_loss.toFixed(2)}</td>
              <td className="py-1 pr-3 text-accent">{r.target1?.toFixed(2) ?? (r.underlying_target1 != null ? `${r.underlying_target1.toFixed(2)} (${r.underlying_symbol})` : "-")}</td>
              <td className="py-1 pr-3">{r.exit_price?.toFixed(2) ?? "-"}</td>
              <td className={`py-1 pr-3 ${(r.pnl ?? 0) >= 0 ? "text-accent" : "text-danger"}`}>
                {r.pnl?.toFixed(2) ?? "-"}
              </td>
              <td className="py-1 pr-3 text-muted" title={r.charges_source === "CONTRACT_NOTE" ? "Broker's actual charges from an uploaded contract note" : "Platform estimate (NSE cost model)"}>
                {r.charges.toFixed(2)} <span className={`text-[10px] uppercase ${r.charges_source === "CONTRACT_NOTE" ? "text-accent" : "text-muted"}`}>{r.charges_source === "CONTRACT_NOTE" ? "actual" : "est."}</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
