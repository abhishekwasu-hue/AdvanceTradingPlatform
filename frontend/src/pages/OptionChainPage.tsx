import { useState } from "react";
import { api } from "../api/client";
import { Card, StatTile } from "../components/ui";
import { DataSourceBar, useCandleSource } from "../components/DataSource";
import type { OptionChainAnalysis } from "../types";
import { generateSampleOptionChain } from "../utils/sampleData";
import { PageHeader } from "../components/primitives";
import OiBanner from "../components/OiBanner";
import OiHistoryTable from "../components/OiHistoryTable";
import OiSettingsCard from "../components/OiSettingsCard";
import StrikeOiChart from "../components/StrikeOiChart";
import { getToken } from "../api/client";

const BIAS_COLOR: Record<string, string> = {
  BULLISH: "text-up",
  BEARISH: "text-down",
  CONFLICTING: "text-warn",
  NEUTRAL: "text-fg-muted",
};

export default function OptionChainPage() {
  const [underlying, setUnderlying] = useState("NIFTY");
  const [ltp, setLtp] = useState(22000);
  const [tilt, setTilt] = useState<"bullish" | "bearish" | "mixed">("bullish");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<OptionChainAnalysis | null>(null);
  const source = useCandleSource();            // Phase AD: live chain through the broker session
  const [dataWarnings, setDataWarnings] = useState<string[]>([]);
  // OI Banner (O3): the collector's view of the underlying last analysed (stored 5-minute snapshots, not this fetch).
  const [oiUnderlying, setOiUnderlying] = useState(underlying.trim().toUpperCase());

  async function analyze() {
    setOiUnderlying(underlying.trim().toUpperCase());
    setLoading(true);
    setError(null);
    try {
      const fetched = await source.fetchChains([underlying], () => generateSampleOptionChain(underlying, ltp, tilt));
      setDataWarnings(fetched.warnings);
      const chain = fetched.chains[underlying.trim().toUpperCase()];
      if (!chain) throw new Error(`No option chain for ${underlying}`);
      const result = await api.analyzeOptionChain(chain);
      setAnalysis(result);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <PageHeader title="Option Chain" description="PCR, Max Pain, ATM/ITM/OTM and a bias that never relies on PCR alone." />

      <DataSourceBar source={source} note="Underlying LTP and tilt shape the sample chain only." />
      {dataWarnings.map((w, i) => <div key={i} className="text-xs text-warn">{w}</div>)}

      <Card>
        <div className="grid sm:grid-cols-4 gap-3 items-end">
          <div>
            <label className="block text-xs text-fg-muted mb-1">Underlying</label>
            <input
              className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm"
              value={underlying}
              onChange={(e) => setUnderlying(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs text-fg-muted mb-1">Underlying LTP</label>
            <input
              type="number"
              className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm"
              value={ltp}
              onChange={(e) => setLtp(Number(e.target.value))}
            />
          </div>
          <div>
            <label className="block text-xs text-fg-muted mb-1">Sample OI tilt</label>
            <select
              className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm"
              value={tilt}
              onChange={(e) => setTilt(e.target.value as typeof tilt)}
            >
              <option value="bullish">Bullish (PCR + OI agree)</option>
              <option value="bearish">Bearish (PCR + OI agree)</option>
              <option value="mixed">Mixed (PCR vs OI disagree)</option>
            </select>
          </div>
          <button
            onClick={analyze}
            disabled={loading}
            className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
          >
            {loading ? "Analyzing…" : "Analyze Chain"}
          </button>
        </div>
      </Card>

      {error && <div className="text-sm text-down">{error}</div>}

      {getToken() && oiUnderlying && (
        <>
          <OiBanner underlying={oiUnderlying} />
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title={`${oiUnderlying} OI history`}><OiHistoryTable underlying={oiUnderlying} /></Card>
            <Card title={`${oiUnderlying} OI by strike`}><StrikeOiChart underlying={oiUnderlying} /></Card>
          </div>
          <Card title="OI banner settings"><OiSettingsCard underlying={oiUnderlying} /></Card>
        </>
      )}

      {analysis && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
            <StatTile label="Bias" value={<span className={BIAS_COLOR[analysis.bias]}>{analysis.bias}</span>} />
            <StatTile label="PCR" value={analysis.pcr?.toFixed(2) ?? "-"} />
            <StatTile label="Max Pain" value={analysis.max_pain ?? "-"} />
            <StatTile label="ATM Strike" value={analysis.atm_strike ?? "-"} />
            <StatTile label="Total Call / Put OI" value={`${analysis.total_call_oi} / ${analysis.total_put_oi}`} />
          </div>

          <Card title="Bias reasons">
            <ul className="text-sm text-fg-muted space-y-1 list-disc list-inside">
              {analysis.bias_reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </Card>

          <Card title="Concentration zones">
            <div className="grid sm:grid-cols-2 gap-4 text-sm">
              <div>
                <div className="text-fg-muted text-xs uppercase mb-1">Call resistance strikes</div>
                <div className="text-fg">{analysis.call_resistance_strikes.join(", ")}</div>
              </div>
              <div>
                <div className="text-fg-muted text-xs uppercase mb-1">Put support strikes</div>
                <div className="text-fg">{analysis.put_support_strikes.join(", ")}</div>
              </div>
            </div>
          </Card>

          <Card title="Strike-wise chain">
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-fg-muted uppercase text-[10px] tracking-wide">
                  <tr className="text-left">
                    <th className="py-1 pr-3">Call OI</th>
                    <th className="py-1 pr-3">Call Activity</th>
                    <th className="py-1 pr-3">Call</th>
                    <th className="py-1 pr-3 text-center">Strike</th>
                    <th className="py-1 pr-3">Put</th>
                    <th className="py-1 pr-3">Put Activity</th>
                    <th className="py-1 pr-3">Put OI</th>
                  </tr>
                </thead>
                <tbody>
                  {analysis.strikes.map((s) => (
                    <tr key={s.strike} className={`border-t border-border ${s.strike === analysis.atm_strike ? "bg-surface-2/60" : ""}`}>
                      <td className="py-1 pr-3">{s.call_oi ?? "-"}</td>
                      <td className="py-1 pr-3">{s.call_activity}</td>
                      <td className="py-1 pr-3">{s.call_moneyness}</td>
                      <td className="py-1 pr-3 text-center font-semibold">{s.strike}</td>
                      <td className="py-1 pr-3">{s.put_moneyness}</td>
                      <td className="py-1 pr-3">{s.put_activity}</td>
                      <td className="py-1 pr-3">{s.put_oi ?? "-"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
