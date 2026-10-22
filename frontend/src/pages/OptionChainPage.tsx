import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/errors";
import { Card, StatTile } from "../components/ui";
import type { OptionChainAnalysis } from "../types";
import { PageHeader } from "../components/primitives";
import OiBanner from "../components/OiBanner";
import OiHistoryTable from "../components/OiHistoryTable";
import OiAlertsCard from "../components/OiAlertsCard";
import OiSettingsCard from "../components/OiSettingsCard";
import StrikeOiChart from "../components/StrikeOiChart";
import { getToken } from "../api/client";
import { chainLinks, contractsAvailable } from "../charting/contracts";

const BIAS_COLOR: Record<string, string> = {
  BULLISH: "text-up",
  BEARISH: "text-down",
  CONFLICTING: "text-warn",
  NEUTRAL: "text-fg-muted",
};

/** Where "the Broker page" is: Settings -> Brokers until the Broker page's own `/broker` route ships (a literal, as
 * the broker gate's CONNECT_BROKER_PATH; that change moves both). */
export const BROKER_PAGE_PATH = "/settings";

/** OC1: what the page says when the chain cannot be read - plain words, never the server's own text. `connect`: the
 * words end with a link to the Broker page. */
export function chainProblem(e: unknown): { text: string; connect: boolean } {
  if (e instanceof ApiError && e.status === 401) return { text: "Sign in to see the option chain.", connect: false };
  if (e instanceof ApiError && e.status === 409 && e.dataTier) return { text: "This symbol is not available live on your plan.", connect: false };
  if (e instanceof ApiError && e.status === 409) return { text: "Connect your broker on the", connect: true };
  return { text: "We could not load the option chain. Try again in a moment.", connect: false };
}

/**
 * The Option chain page (today's layout, `ui_v2` off). OC1: the chain is read from the trader's own broker session
 * as soon as the page opens; there is no other source of data and no price to type in. The v2 page (src/optionChain)
 * replaces it once the owner switches it on.
 */
export default function OptionChainPage() {
  // A deep link (?underlying=, e.g. from an OI alert) opens that underlying; otherwise the page default.
  const [underlying, setUnderlying] = useState(() => new URLSearchParams(window.location.search).get("underlying")?.trim() || "NIFTY");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<{ text: string; connect: boolean } | null>(null);
  const [analysis, setAnalysis] = useState<OptionChainAnalysis | null>(null);
  // OI Banner (O3): the collector's view of the underlying last analysed (stored 5-minute snapshots, not this fetch).
  const [oiUnderlying, setOiUnderlying] = useState(underlying.trim().toUpperCase());
  // N4 CH4b: chain -> chart links (flag chart_contracts; a live chain only)
  const [linkChain, setLinkChain] = useState<{ underlying: string; expiry: string } | null>(null);

  async function analyze() {
    const name = underlying.trim().toUpperCase();
    if (!name) return;
    setOiUnderlying(name);
    setLoading(true);
    setError(null);
    try {
      const fetched = await api.marketDataOptionChains([name]);
      const chain = fetched.symbols[name]?.chain;
      if (!chain || !chain.rows.length) throw new Error("no chain");
      const result = await api.analyzeOptionChain(chain);
      setAnalysis(result);
      setLinkChain((await contractsAvailable(chain.underlying)) ? { underlying: chain.underlying, expiry: chain.expiry } : null);
    } catch (e) {
      setAnalysis(null);
      setError(chainProblem(e));
    } finally {
      setLoading(false);
    }
  }

  // Everything happens on its own: the chain of the page's underlying is read once on open.
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    if (getToken()) void analyze();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="space-y-4">
      <PageHeader title="Option Chain" description="PCR, Max Pain, ATM/ITM/OTM and a bias that never relies on PCR alone." />

      <Card>
        <form className="flex flex-wrap items-end gap-3" onSubmit={(e) => { e.preventDefault(); void analyze(); }}>
          <div className="min-w-0 flex-1 sm:max-w-xs">
            <label htmlFor="option-chain-underlying" className="block text-xs text-fg-muted mb-1">Underlying</label>
            <input
              id="option-chain-underlying"
              className="w-full rounded bg-surface-2 border border-border px-2 py-1.5 text-sm"
              value={underlying}
              onChange={(e) => setUnderlying(e.target.value)}
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-4 py-1.5 text-sm disabled:opacity-50"
          >
            {loading ? "Loading…" : "Show chain"}
          </button>
        </form>
      </Card>

      {error && (
        <p className="text-sm text-fg-muted" role="status">
          {error.text}
          {error.connect && <> <a href={BROKER_PAGE_PATH} className="text-brand underline">Broker page</a>.</>}
        </p>
      )}

      {getToken() && oiUnderlying && (
        <>
          <OiBanner underlying={oiUnderlying} />
          <div className="grid gap-4 lg:grid-cols-2">
            <Card title={`${oiUnderlying} OI history`}><OiHistoryTable underlying={oiUnderlying} /></Card>
            <Card title={`${oiUnderlying} OI by strike`}><StrikeOiChart underlying={oiUnderlying} /></Card>
          </div>
          <Card title="OI banner settings"><OiSettingsCard underlying={oiUnderlying} /></Card>
          <Card title="OI banner alerts"><OiAlertsCard underlying={oiUnderlying} /></Card>
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
                <thead className="text-fg-muted uppercase text-xs tracking-wide">
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
                  {analysis.strikes.map((s) => {
                    const links = chainLinks(linkChain, s.strike);
                    return (
                    <tr key={s.strike} className={`border-t border-border ${s.strike === analysis.atm_strike ? "bg-surface-2/60" : ""}`}>
                      <td className="py-1 pr-3">{s.call_oi ?? "-"}</td>
                      <td className="py-1 pr-3">{s.call_activity}</td>
                      <td className="py-1 pr-3">{s.call_moneyness}
                        {links && <a href={links.ce} target="_blank" rel="noopener" className="ml-1 text-brand underline" aria-label={`Chart of the ${s.strike} call`}>chart</a>}</td>
                      <td className="py-1 pr-3 text-center font-semibold">{s.strike}</td>
                      <td className="py-1 pr-3">{s.put_moneyness}
                        {links && <a href={links.pe} target="_blank" rel="noopener" className="ml-1 text-brand underline" aria-label={`Chart of the ${s.strike} put`}>chart</a>}</td>
                      <td className="py-1 pr-3">{s.put_activity}</td>
                      <td className="py-1 pr-3">{s.put_oi ?? "-"}</td>
                    </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        </>
      )}
    </div>
  );
}
