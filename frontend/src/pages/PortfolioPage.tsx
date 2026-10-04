import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import EquityCurveChart from "../components/EquityCurveChart";
import { Card, StatTile } from "../components/ui";
import type { PortfolioExposure, TradeRecord } from "../types";

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
        <h1 className="text-xl font-extrabold text-rose-400">Portfolio</h1>
        <Card>
          <p className="text-sm text-muted">Log in from the Account tab to see your portfolio overview.</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-extrabold text-rose-400">Portfolio</h1>
        <p className="text-sm font-semibold text-rose-200">Capital deployed across open positions and cumulative realized P&amp;L.</p>
      </div>

      {error && <div className="text-sm text-danger">{error}</div>}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatTile label="Open Positions" value={positions.length} />
        <StatTile label="Capital Deployed" value={capitalDeployed.toFixed(2)} />
        <StatTile label="Realized P&L" value={realizedPnl.toFixed(2)} tone={realizedPnl >= 0 ? "up" : "down"} />
        <StatTile label="Symbols Held" value={allocationBySymbol.length} />
      </div>

      <ExposureCard />

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


/** Phase M / V4.4: the portfolio engine's view - gross/net notional, concentration, risk at the stops. */
function ExposureCard() {
  const [exposure, setExposure] = useState<PortfolioExposure | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => { api.portfolioExposure().then(setExposure).catch((e) => setErr(String(e))); }, []);
  if (err) return <Card title="Exposure"><div className="text-xs text-danger">{err}</div></Card>;
  if (!exposure) return null;
  return (
    <Card title={`Exposure (${exposure.price_source === "ltp" ? "live prices" : "entry prices - no broker session"})`}>
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-3">
        <StatTile label="Gross notional" value={`${exposure.gross_notional.toFixed(0)} (${exposure.gross_pct_of_capital.toFixed(0)}%)`} />
        <StatTile label="Net notional" value={exposure.net_notional.toFixed(0)} tone={exposure.net_notional >= 0 ? "up" : "down"} />
        <StatTile label="Unrealised P&L" value={exposure.unrealised_pnl.toFixed(0)} tone={exposure.unrealised_pnl >= 0 ? "up" : "down"} />
        <StatTile label="Risk at stops" value={`${exposure.risk_at_stops.toFixed(0)} (${exposure.risk_pct_of_capital.toFixed(1)}%)`} tone="down" />
        <StatTile label="Largest symbol" value={`${exposure.largest_symbol_pct.toFixed(0)}% of capital`} />
      </div>
      {exposure.warnings.length > 0 && <ul className="text-xs text-amber-300 list-disc pl-4 mb-2">{exposure.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
      {exposure.by_symbol.length > 0 && (
        <table className="w-full text-xs"><thead className="text-muted uppercase text-[10px]"><tr className="text-left"><th className="py-1 pr-3">Symbol</th><th className="py-1 pr-3">Positions</th><th className="py-1 pr-3">Net qty</th><th className="py-1 pr-3">Notional</th><th className="py-1 pr-3">% capital</th><th className="py-1 pr-3">Unrealised</th><th className="py-1 pr-3">Risk at stop</th><th className="py-1 pr-3">Strategies</th></tr></thead>
          <tbody>{exposure.by_symbol.map((r) => (
            <tr key={r.symbol} className="border-t border-border/60"><td className="py-1 pr-3 font-medium">{r.symbol}</td><td className="py-1 pr-3">{r.positions}</td><td className="py-1 pr-3">{r.quantity}</td><td className="py-1 pr-3">{r.notional.toFixed(0)}</td><td className={`py-1 pr-3 ${r.pct_of_capital > 40 ? "text-amber-400" : ""}`}>{r.pct_of_capital.toFixed(1)}%</td><td className={`py-1 pr-3 ${r.unrealised_pnl >= 0 ? "text-accent" : "text-danger"}`}>{r.unrealised_pnl.toFixed(0)}</td><td className="py-1 pr-3 text-danger">{r.risk_at_stop.toFixed(0)}</td><td className="py-1 pr-3 text-muted">{r.strategies.join(", ")}</td></tr>
          ))}</tbody></table>
      )}
      <div className="text-[11px] text-muted mt-2">Set portfolio-wide caps (max gross exposure, max symbol concentration) under Risk Management → limits, scope "Portfolio".</div>
    </Card>
  );
}
