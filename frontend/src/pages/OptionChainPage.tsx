import { useState } from "react";
import { api } from "../api/client";
import { Card, DemoDataBanner, StatTile } from "../components/ui";
import type { OptionChainAnalysis } from "../types";
import { generateSampleOptionChain } from "../utils/sampleData";

const BIAS_COLOR: Record<string, string> = {
  BULLISH: "text-accent",
  BEARISH: "text-danger",
  CONFLICTING: "text-warn",
  NEUTRAL: "text-muted",
};

export default function OptionChainPage() {
  const [underlying, setUnderlying] = useState("NIFTY");
  const [ltp, setLtp] = useState(22000);
  const [tilt, setTilt] = useState<"bullish" | "bearish" | "mixed">("bullish");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [analysis, setAnalysis] = useState<OptionChainAnalysis | null>(null);

  async function analyze() {
    setLoading(true);
    setError(null);
    try {
      const chain = generateSampleOptionChain(underlying, ltp, tilt);
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
      <div>
        <h1 className="text-xl font-extrabold text-fuchsia-400">Option Chain</h1>
        <p className="text-sm font-semibold text-fuchsia-400/60">PCR, Max Pain, ATM/ITM/OTM and a bias that never relies on PCR alone.</p>
      </div>

      <DemoDataBanner />

      <Card>
        <div className="grid sm:grid-cols-4 gap-3 items-end">
          <div>
            <label className="block text-xs text-muted mb-1">Underlying</label>
            <input
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={underlying}
              onChange={(e) => setUnderlying(e.target.value)}
            />
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Underlying LTP</label>
            <input
              type="number"
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
              value={ltp}
              onChange={(e) => setLtp(Number(e.target.value))}
            />
          </div>
          <div>
            <label className="block text-xs text-muted mb-1">Sample OI tilt</label>
            <select
              className="w-full rounded bg-panel2 border border-border px-2 py-1.5 text-sm"
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
            className="rounded bg-brand hover:bg-brand-dim text-white font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
          >
            {loading ? "Analyzing…" : "Analyze Chain"}
          </button>
        </div>
      </Card>

      {error && <div className="text-sm text-danger">{error}</div>}

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
            <ul className="text-sm text-slate-300 space-y-1 list-disc list-inside">
              {analysis.bias_reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </Card>

          <Card title="Concentration zones">
            <div className="grid sm:grid-cols-2 gap-4 text-sm">
              <div>
                <div className="text-muted text-xs uppercase mb-1">Call resistance strikes</div>
                <div className="text-slate-200">{analysis.call_resistance_strikes.join(", ")}</div>
              </div>
              <div>
                <div className="text-muted text-xs uppercase mb-1">Put support strikes</div>
                <div className="text-slate-200">{analysis.put_support_strikes.join(", ")}</div>
              </div>
            </div>
          </Card>

          <Card title="Strike-wise chain">
            <div className="overflow-x-auto">
              <table className="w-full text-xs">
                <thead className="text-muted uppercase text-[10px] tracking-wide">
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
                    <tr key={s.strike} className={`border-t border-border ${s.strike === analysis.atm_strike ? "bg-panel2/60" : ""}`}>
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
