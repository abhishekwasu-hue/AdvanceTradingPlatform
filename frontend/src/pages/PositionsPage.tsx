import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card, DirectionBadge, StatTile } from "../components/ui";
import type { TradeRecord } from "../types";

export default function PositionsPage() {
  const { user, loading: authLoading } = useAuth();
  const [positions, setPositions] = useState<TradeRecord[]>([]);
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [priceInputs, setPriceInputs] = useState<Record<number, string>>({});
  const [markMessages, setMarkMessages] = useState<Record<number, string>>({});

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
                  <span className="text-muted text-xs">{p.strategy_id}</span>
                  <span className="text-xs text-muted">Entry {p.entry_price.toFixed(2)} × {p.quantity}</span>
                  <span className="text-xs text-danger">SL {p.stop_loss.toFixed(2)}</span>
                  <span className="text-xs text-accent">T1 {p.target1.toFixed(2)}</span>
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
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-border">
              <td className="py-1 pr-3 font-medium text-slate-200">{r.symbol}</td>
              <td className="py-1 pr-3 text-muted">{r.strategy_id}</td>
              <td className="py-1 pr-3">
                <DirectionBadge direction={r.direction as "LONG" | "SHORT"} />
              </td>
              <td className="py-1 pr-3">{r.entry_price.toFixed(2)}</td>
              <td className="py-1 pr-3">{r.quantity}</td>
              <td className="py-1 pr-3 text-danger">{r.stop_loss.toFixed(2)}</td>
              <td className="py-1 pr-3 text-accent">{r.target1.toFixed(2)}</td>
              <td className="py-1 pr-3">{r.exit_price?.toFixed(2) ?? "-"}</td>
              <td className={`py-1 pr-3 ${(r.pnl ?? 0) >= 0 ? "text-accent" : "text-danger"}`}>
                {r.pnl?.toFixed(2) ?? "-"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
