import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import EquityCurveChart from "../components/EquityCurveChart";
import { Card, StatTile, signTone } from "../components/ui";
import type { PortfolioExposure, TradeRecord } from "../types";
import { EmptyState, PageHeader, Signed, Table } from "../components/primitives";

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
        <PageHeader title="Portfolio" />
        <Card>
          <p className="text-sm text-fg-muted">Log in from the Account tab to see your portfolio overview.</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Portfolio" description={<>Capital deployed across open positions and cumulative realized P&amp;L.</>} />

      {error && <div className="text-sm text-down">{error}</div>}

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatTile label="Open Positions" value={positions.length} />
        <StatTile label="Capital Deployed" value={capitalDeployed.toFixed(2)} />
        <StatTile label="Realized P&L" value={realizedPnl.toFixed(2)} tone={signTone(realizedPnl)} />
        <StatTile label="Symbols Held" value={allocationBySymbol.length} />
      </div>

      <ExposureCard />

      <Card title="Cumulative realized P&amp;L over time">
        <EquityCurveChart equity={equityCurve} />
      </Card>

      <Card title="Allocation by symbol (open positions)">
        {allocationBySymbol.length === 0 ? (
          <EmptyState title="No open positions" body="Capital shows here once a paper or live position is open." />
        ) : (
          <div className="space-y-2">
            {allocationBySymbol.map(([symbol, amount]) => {
              const pct = capitalDeployed > 0 ? (amount / capitalDeployed) * 100 : 0;
              return (
                <div key={symbol}>
                  <div className="flex justify-between text-xs mb-1">
                    <span className="text-fg font-medium">{symbol}</span>
                    <span className="text-fg-muted">{amount.toFixed(2)} ({pct.toFixed(0)}%)</span>
                  </div>
                  <div className="h-1.5 w-full rounded-full bg-surface-2">
                    <div className="h-1.5 rounded-full bg-brand" style={{ width: `${pct}%` }} />
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
  if (err) return <Card title="Exposure"><div className="text-xs text-down">{err}</div></Card>;
  if (!exposure) return null;
  return (
    <Card title={`Exposure (${exposure.price_source === "ltp" ? "live prices" : "entry prices - no broker session"})`}>
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 mb-3">
        <StatTile label="Gross notional" value={`${exposure.gross_notional.toFixed(0)} (${exposure.gross_pct_of_capital.toFixed(0)}%)`} />
        <StatTile label="Net notional" value={exposure.net_notional.toFixed(0)} />
        <StatTile label="Unrealised P&L" value={exposure.unrealised_pnl.toFixed(0)} tone={signTone(exposure.unrealised_pnl, 0)} />
        <StatTile label="Risk at stops" value={`${exposure.risk_at_stops.toFixed(0)} (${exposure.risk_pct_of_capital.toFixed(1)}%)`} tone={exposure.risk_at_stops > 0 ? "down" : "default"} />
        <StatTile label="Largest symbol" value={`${exposure.largest_symbol_pct.toFixed(0)}% of capital`} />
      </div>
      {exposure.warnings.length > 0 && <ul className="text-xs text-warn list-disc pl-4 mb-2">{exposure.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul>}
      {exposure.by_symbol.length > 0 && (
        <Table
          caption="Exposure by symbol"
          rows={exposure.by_symbol}
          rowKey={(r) => r.symbol}
          columns={[
            { key: "symbol", header: "Symbol", cell: (r) => <span className="font-medium">{r.symbol}</span> },
            { key: "positions", header: "Positions", numeric: true, cell: (r) => r.positions },
            { key: "qty", header: "Net qty", numeric: true, cell: (r) => r.quantity },
            { key: "notional", header: "Notional", numeric: true, cell: (r) => r.notional.toFixed(0) },
            { key: "pct", header: "% capital", numeric: true, cell: (r) => <span className={r.pct_of_capital > 40 ? "text-warn" : ""}>{r.pct_of_capital.toFixed(1)}%</span> },
            { key: "upnl", header: "Unrealised", numeric: true, cell: (r) => <Signed value={r.unrealised_pnl} format={(v) => v.toFixed(0)} /> },
            { key: "risk", header: "Risk at stop", numeric: true, cell: (r) => <span className={r.risk_at_stop > 0 ? "text-down" : ""}>{r.risk_at_stop.toFixed(0)}</span> },
            { key: "strategies", header: "Strategies", cell: (r) => <span className="text-fg-muted">{r.strategies.join(", ")}</span> },
          ]}
        />
      )}
      <div className="text-[11px] text-fg-muted mt-2">Set portfolio-wide caps (max gross exposure, max symbol concentration) under Risk Management → limits, scope "Portfolio".</div>
    </Card>
  );
}
