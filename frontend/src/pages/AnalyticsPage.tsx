import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card, StatTile } from "../components/ui";
import type { AnalyticsSummary, GroupStats } from "../types";

function GroupTable({ title, rows }: { title: string; rows: GroupStats[] }) {
  return (
    <Card title={title}>
      {rows.length === 0 ? (
        <div className="text-sm text-muted py-2">No closed trades yet.</div>
      ) : (
        <table className="w-full text-xs">
          <thead className="text-muted uppercase text-[10px] tracking-wide">
            <tr className="text-left">
              <th className="py-1 pr-3">Key</th>
              <th className="py-1 pr-3">Trades</th>
              <th className="py-1 pr-3">Win Rate</th>
              <th className="py-1 pr-3">Net P&amp;L</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key} className="border-t border-border">
                <td className="py-1 pr-3 font-medium text-slate-200">{r.key}</td>
                <td className="py-1 pr-3">{r.trades}</td>
                <td className="py-1 pr-3">{r.win_rate.toFixed(1)}%</td>
                <td className={`py-1 pr-3 ${r.net_pnl >= 0 ? "text-accent" : "text-danger"}`}>{r.net_pnl.toFixed(2)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Card>
  );
}

export default function AnalyticsPage() {
  const { user, loading: authLoading } = useAuth();
  const [summary, setSummary] = useState<AnalyticsSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    api.getAnalyticsSummary().then(setSummary).catch((e) => setError(String(e)));
  }, [user]);

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <h1 className="text-xl font-semibold text-slate-100">Analytics</h1>
        <Card>
          <p className="text-sm text-muted">
            Log in from the Account tab to see win rate and P&amp;L broken down by strategy and
            symbol, computed from your persisted trade history.
          </p>
        </Card>
      </div>
    );
  }

  if (error) return <div className="text-sm text-danger">{error}</div>;
  if (!summary) return <div className="text-sm text-muted">Loading…</div>;

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-semibold text-slate-100">Analytics</h1>
        <p className="text-sm text-muted">Aggregated from your full persisted trade history (Positions/Trade Journal).</p>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatTile label="Total Trades" value={summary.total_trades} />
        <StatTile label="Closed / Open" value={`${summary.closed_trades} / ${summary.open_trades}`} />
        <StatTile label="Win Rate" value={`${summary.win_rate.toFixed(1)}%`} />
        <StatTile label="Net P&L" value={summary.net_pnl.toFixed(2)} tone={summary.net_pnl >= 0 ? "up" : "down"} />
        <StatTile label="Gross Profit" value={summary.gross_profit.toFixed(2)} tone="up" />
        <StatTile label="Gross Loss" value={summary.gross_loss.toFixed(2)} tone="down" />
        <StatTile label="Profit Factor" value={summary.profit_factor?.toFixed(2) ?? "-"} />
        <StatTile label="Avg Win / Loss" value={`${summary.avg_win.toFixed(2)} / ${summary.avg_loss.toFixed(2)}`} />
      </div>

      <GroupTable title="By Strategy" rows={summary.by_strategy} />
      <GroupTable title="By Symbol" rows={summary.by_symbol} />
    </div>
  );
}
