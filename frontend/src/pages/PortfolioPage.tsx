import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import EquityCurveChart from "../components/EquityCurveChart";
import { Card, StatTile } from "../components/ui";
import type { TradeRecord } from "../types";

export default function PortfolioPage() {
  const { user, loading: authLoading } = useAuth();
  const [positions, setPositions] = useState<TradeRecord[]>([]);
  const [trades, setTrades] = useState<TradeRecord[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    Promise.all([api.listPositions(), api.listTrades()])
      .then(([p, t]) => {
        setPositions(p);
        setTrades(t);
      })
      .catch((e) => setError(String(e)));
  }, [user]);

  const capitalDeployed = useMemo(
    () => positions.reduce((sum, p) => sum + p.entry_price * p.quantity, 0),
    [positions],
  );

  const allocationBySymbol = useMemo(() => {
    const map = new Map<string, number>();
    for (const p of positions) {
      map.set(p.symbol, (map.get(p.symbol) ?? 0) + p.entry_price * p.quantity);
    }
    return [...map.entries()].sort((a, b) => b[1] - a[1]);
  }, [positions]);

  const equityCurve = useMemo(() => {
    const closed = trades
      .filter((t) => t.exit_time !== null)
      .sort((a, b) => new Date(a.exit_time!).getTime() - new Date(b.exit_time!).getTime());
    let equity = 0;
    return closed.map((t) => (equity += t.pnl ?? 0));
  }, [trades]);

  const realizedPnl = trades.reduce((sum, t) => sum + (t.pnl ?? 0), 0);

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold text-slate-100">Portfolio</h1>
        <Card>
          <p className="text-sm text-muted">Log in from the Account tab to see your portfolio overview.</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Portfolio</h1>
        <p className="text-sm text-muted">Capital deployed across open positions and cumulative realized P&amp;L.</p>
      </div>

      {error && <div className="text-sm text-danger">{error}</div>}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatTile label="Open Positions" value={positions.length} />
        <StatTile label="Capital Deployed" value={capitalDeployed.toFixed(2)} />
        <StatTile label="Realized P&L" value={realizedPnl.toFixed(2)} tone={realizedPnl >= 0 ? "up" : "down"} />
        <StatTile label="Symbols Held" value={allocationBySymbol.length} />
      </div>

      <Card title="Cumulative realized P&amp;L over time">
        <EquityCurveChart equity={equityCurve} />
      </Card>

      <Card title="Allocation by symbol (open positions)">
        {allocationBySymbol.length === 0 ? (
          <div className="text-sm text-muted py-2">No open positions.</div>
        ) : (
          <div className="space-y-2">
            {allocationBySymbol.map(([symbol, amount]) => {
              const pct = capitalDeployed > 0 ? (amount / capitalDeployed) * 100 : 0;
              return (
                <div key={symbol}>
                  <div className="flex justify-between text-xs mb-1">
                    <span className="text-slate-200 font-medium">{symbol}</span>
                    <span className="text-muted">{amount.toFixed(2)} ({pct.toFixed(0)}%)</span>
                  </div>
                  <div className="h-1.5 w-full rounded-full bg-panel2">
                    <div className="h-1.5 rounded-full bg-accent" style={{ width: `${pct}%` }} />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Card>
    </div>
  );
}
