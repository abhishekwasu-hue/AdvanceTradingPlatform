import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Card, StatTile, signClass, signTone } from "../components/ui";
import type { AnalyticsSummary, DegradationReport, GroupStats, TaxReport } from "../types";
import { getToken } from "../api/client";
import { apiErrorFrom, friendlyError } from "../api/errors";

import { PageHeader } from "../components/primitives";
function TaxReportCard() {
  const [years, setYears] = useState<string[]>([]);
  const [fy, setFy] = useState<string>("");
  const [mode, setMode] = useState<string>("LIVE");
  const [report, setReport] = useState<TaxReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.taxYears().then((y) => { setYears(y.years); setFy((f) => f || y.current); }).catch((e) => setError(String(e))); }, []);
  useEffect(() => { if (fy) api.taxReport(fy, mode).then(setReport).catch((e) => setError(String(e))); }, [fy, mode]);
  const money = (v: number) => v.toLocaleString("en-IN", { maximumFractionDigits: 0 });
  async function download() {
    setError(null);
    let res: Response;
    try { res = await fetch(api.taxReportCsvUrl(fy, mode), { headers: { Authorization: `Bearer ${getToken() ?? ""}` } }); } catch (e) { setError(friendlyError(e)); return; }
    // P1.1: a refused download shows the reason instead of saving the error body as the CSV.
    if (!res.ok) { setError((await apiErrorFrom(res)).message); return; }
    const blob = await res.blob();
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = `tax-report-${fy}-${mode}.csv`; a.click();
  }
  return (
    <Card title="Tax report (financial year)">
      <div className="flex flex-wrap items-center gap-2 text-xs mb-3">
        <label className="text-fg-muted">FY <select className="rounded bg-surface-2 border border-border px-1 py-0.5 ml-1" value={fy} onChange={(e) => setFy(e.target.value)}>{years.map((y) => <option key={y}>{y}</option>)}</select></label>
        <label className="text-fg-muted">Trades <select className="rounded bg-surface-2 border border-border px-1 py-0.5 ml-1" value={mode} onChange={(e) => setMode(e.target.value)}>{["LIVE", "PAPER", "ALL"].map((m) => <option key={m}>{m}</option>)}</select></label>
        <button onClick={download} disabled={!report} className="rounded border border-border hover:bg-surface-2 text-fg px-3 py-1 disabled:opacity-50">Download CSV</button>
        {report && <span className="text-fg-muted">{report.trades} closed trades · net {money(report.net_pnl)}</span>}
      </div>
      {error && <div className="text-xs text-down">{error}</div>}
      {report && (
        <table className="w-full text-xs">
          <thead className="text-fg-muted uppercase text-[10px]"><tr className="text-left"><th className="py-1 pr-3">Head</th><th className="py-1 pr-3">Trades</th><th className="py-1 pr-3">Gross</th><th className="py-1 pr-3">Charges</th><th className="py-1 pr-3">Net</th><th className="py-1 pr-3">Turnover</th><th className="py-1 pr-3">STT/CTT est.</th><th className="py-1 pr-3">Crypto TDS / 30% tax</th></tr></thead>
          <tbody>
            {(Object.entries(report.classes) as [string, TaxReport["classes"]["FNO"]][]).map(([k, c]) => (
              <tr key={k} className="border-t border-border/60">
                <td className="py-1 pr-3"><div className="font-medium">{k.replace("_", " ")}</div><div className="text-[10px] text-fg-muted">{c.income_head}</div></td>
                <td className="py-1 pr-3">{c.trades}</td>
                <td className={`py-1 pr-3 ${signClass(c.gross_pnl)}`}>{money(c.gross_pnl)}</td>
                <td className="py-1 pr-3">{money(c.charges)}</td>
                <td className={`py-1 pr-3 font-semibold ${signClass(c.net_pnl)}`}>{money(c.net_pnl)}</td>
                <td className="py-1 pr-3">{money(c.turnover)}</td>
                <td className="py-1 pr-3">{money(c.stt_estimate + c.ctt_estimate)}</td>
                <td className="py-1 pr-3">{k === "CRYPTO" ? `${money(c.tds_estimate)} / ${money(c.tax_estimate)}${c.disallowed_losses ? ` (losses ${money(c.disallowed_losses)} not set off)` : ""}` : "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {report && <ul className="mt-2 space-y-0.5 text-[11px] text-fg-muted list-disc pl-4">{report.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
    </Card>
  );
}

function GroupTable({ title, rows }: { title: string; rows: GroupStats[] }) {
  return (
    <Card title={title}>
      {rows.length === 0 ? (
        <div className="text-sm text-fg-muted py-2">No closed trades yet.</div>
      ) : (
        <table className="w-full text-xs">
          <thead className="text-fg-muted uppercase text-[10px] tracking-wide">
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
                <td className="py-1 pr-3 font-medium text-fg">{r.key}</td>
                <td className="py-1 pr-3">{r.trades}</td>
                <td className="py-1 pr-3">{r.win_rate.toFixed(1)}%</td>
                <td className={`py-1 pr-3 ${signClass(r.net_pnl)}`}>{r.net_pnl.toFixed(2)}</td>
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
  const [degradation, setDegradation] = useState<DegradationReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    api.getAnalyticsSummary().then(setSummary).catch((e) => setError(String(e)));
    api.analyticsDegradation().then(setDegradation).catch(() => {});
  }, [user]);

  if (authLoading) return null;

  if (!user) {
    return (
      <div className="space-y-4">
        <PageHeader title="Analytics" />
        <Card>
          <p className="text-sm text-fg-muted">
            Log in from the Account tab to see win rate and P&amp;L broken down by strategy and
            symbol, computed from your persisted trade history.
          </p>
        </Card>
      </div>
    );
  }

  if (error) return <div className="text-sm text-down">{error}</div>;
  if (!summary) return <div className="text-sm text-fg-muted">Loading…</div>;

  return (
    <div className="space-y-4">
      {degradation && degradation.strategies.length > 0 && (
        <Card title={`Live vs backtest (${degradation.degraded} degraded, ${degradation.watch} on watch)`}>
          <table className="w-full text-xs"><thead className="text-fg-muted uppercase text-[10px]"><tr className="text-left"><th className="py-1 pr-3">Strategy</th><th className="py-1 pr-3">Status</th><th className="py-1 pr-3">Live trades</th><th className="py-1 pr-3">Live win rate</th><th className="py-1 pr-3">Recent-20 win rate</th><th className="py-1 pr-3">Backtest win rate</th><th className="py-1 pr-3">Live expectancy</th><th className="py-1 pr-3">Why</th></tr></thead>
            <tbody>{degradation.strategies.map((r) => (
              <tr key={r.strategy_id} className="border-t border-border/60">
                <td className="py-1 pr-3 font-medium">{r.strategy_id}</td>
                <td className={`py-1 pr-3 font-bold ${r.status === "DEGRADED" ? "text-down" : r.status === "WATCH" ? "text-warn" : r.status === "OK" ? "text-up" : "text-fg-muted"}`}>{r.status.replace("_", " ")}</td>
                <td className="py-1 pr-3">{r.live.trades}</td>
                <td className="py-1 pr-3">{r.live.win_rate != null ? `${(r.live.win_rate * 100).toFixed(0)}%` : "-"}</td>
                <td className="py-1 pr-3">{r.recent_20.win_rate != null ? `${(r.recent_20.win_rate * 100).toFixed(0)}%` : "-"}</td>
                <td className="py-1 pr-3">{r.backtest?.win_rate != null ? `${(r.backtest.win_rate * 100).toFixed(0)}%` : "-"}</td>
                <td className={`py-1 pr-3 ${signClass(r.live.expectancy)}`}>{r.live.expectancy?.toFixed(1) ?? "-"}</td>
                <td className="py-1 pr-3 text-fg-muted">{r.reasons.join("; ")}</td>
              </tr>
            ))}</tbody></table>
          <div className="text-[11px] text-fg-muted mt-2">{degradation.note}</div>
        </Card>
      )}
      <TaxReportCard />
      <PageHeader title="Analytics" description="Aggregated from your full persisted trade history (Positions/Trade Journal)." />

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <StatTile label="Total Trades" value={summary.total_trades} />
        <StatTile label="Closed / Open" value={`${summary.closed_trades} / ${summary.open_trades}`} />
        <StatTile label="Win Rate" value={`${summary.win_rate.toFixed(1)}%`} />
        <StatTile label="Net P&L" value={summary.net_pnl.toFixed(2)} tone={signTone(summary.net_pnl)} />
        <StatTile label="Gross Profit" value={summary.gross_profit.toFixed(2)} tone={signTone(summary.gross_profit)} />
        <StatTile label="Gross Loss" value={summary.gross_loss.toFixed(2)} tone={summary.gross_loss !== 0 ? "down" : "default"} />
        <StatTile label="Profit Factor" value={summary.profit_factor?.toFixed(2) ?? "-"} />
        <StatTile label="Avg Win / Loss" value={`${summary.avg_win.toFixed(2)} / ${summary.avg_loss.toFixed(2)}`} />
      </div>

      <GroupTable title="By Strategy" rows={summary.by_strategy} />
      <GroupTable title="By Symbol" rows={summary.by_symbol} />
    </div>
  );
}
