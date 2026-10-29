/**
 * N4 CH4b: a contract or a spread in its own browser tab (`/?contract=<leg>&contract=<leg>`, opened from the option chain).
 * Daily bars from the shared history, each leg's open interest, and - for options - a COMPUTED IV, labelled so.
 * Reference only: there is no order control here (flag `chart_contracts`; off -> says so).
 */
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import ProChart from "../components/ProChart";
import { contractsApi, ivText, legName, toCandles, type ContractSeries, type SeriesLeg } from "../charting/contracts";
import { basisAvailable, basisUrl } from "../charting/futuresBasis";
import { SkeletonRows } from "../components/primitives";

/** One leg's OI as small bars (the last 60 days), with its last value - numbers from the series, never from pixels. */
export function OiBars({ leg, days }: { leg: SeriesLeg; days: string[] }) {
  const n = Math.min(60, leg.oi.length);
  const vals = leg.oi.slice(-n);
  const max = Math.max(1, ...vals.map((v) => v ?? 0));
  const last = vals.length ? vals[vals.length - 1] : null;
  return (
    <figure className="flex items-end gap-3 text-xs text-fg-muted" data-testid="oi-bars">
      <svg width={n * 4} height={32} role="img" aria-label={`Open interest of ${legName(leg)} over ${n} days, last ${last ?? "n/a"}`}>
        {vals.map((v, i) => (v == null ? null : <rect key={days[days.length - n + i]} x={i * 4} y={32 - (v / max) * 32} width={3} height={(v / max) * 32} fill="rgb(var(--chart-text))" opacity={0.6} />))}
      </svg>
      <figcaption><span className="text-fg">{legName(leg)}</span> OI {last == null ? "n/a" : last.toLocaleString("en-IN")}
        {leg.iv && <> · IV (computed) {ivText(leg.iv[leg.iv.length - 1])}</>}</figcaption>
    </figure>
  );
}

/** N4 CH4c: a single futures contract links to its underlying's basis / carry / rollover page (only when that is on). */
export function basisLinkFor(series: ContractSeries): string | null {
  return series.legs.length === 1 && series.legs[0].kind === "FUT" ? basisUrl(series.legs[0].underlying) : null;
}

export function ContractPanel({ series, basisLink = null }: { series: ContractSeries; basisLink?: string | null }) {
  return (
    <div className="mt-2 flex flex-col gap-2" data-testid="contract-panel">
      {basisLink && <a className="text-xs text-brand underline" href={basisLink} target="_blank" rel="noreferrer" data-testid="basis-link">Basis, carry and rollover of this underlying</a>}
      {series.legs.map((leg) => <OiBars key={leg.key} leg={leg} days={series.days} />)}
      {series.iv_note && <p className="text-xs text-fg-muted" data-testid="iv-note">{series.iv_note}</p>}
      <p className="text-xs text-fg-muted">{series.legs.length > 1 ? "Spread close = the sum of each leg's close x quantity, on days every leg traded. " : ""}{series.note}</p>
    </div>
  );
}

export default function ContractChartWindow({ params }: { params: URLSearchParams }) {
  const { user, loading } = useAuth();
  const legs = useMemo(() => params.getAll("contract").map((l) => l.trim()).filter(Boolean), [params]);
  const [series, setSeries] = useState<ContractSeries | null>(null);
  const [error, setError] = useState<string | null>(null);
  const title = series ? series.legs.map(legName).join("  ") : legs.join("  ");
  useEffect(() => { document.title = `${title} · AiAlgoDesk`; }, [title]);
  useEffect(() => {
    if (!user || !legs.length) return;
    let live = true;
    contractsApi.series(legs).then(
      (s) => { if (live) setSeries(s); },
      (e: unknown) => { if (live) setError(e instanceof ApiError && e.status === 503 ? "Contract charts are not enabled for your organisation." : String(e)); },
    );
    return () => { live = false; };
  }, [user, legs]);
  const candles = useMemo(() => (series ? toCandles(series) : []), [series]);
  const [basisLink, setBasisLink] = useState<string | null>(null);
  useEffect(() => {
    const link = series ? basisLinkFor(series) : null;
    if (!link || !series) return;
    let live = true;
    basisAvailable(series.legs[0].underlying).then((on) => { if (live) setBasisLink(on ? link : null); }, () => undefined);
    return () => { live = false; };
  }, [series]);

  if (loading) return <SkeletonRows rows={2} label="Loading" />;
  if (!user) return <div className="p-6 text-sm text-fg">Log in in the main tab first, then open the chart again.</div>;
  if (!legs.length) return <div className="p-6 text-sm text-fg">No contract given.</div>;
  return (
    <div className="min-h-screen bg-surface p-4">
      <h1 className="mb-2 text-sm font-semibold text-fg">{title}</h1>
      {error && <p role="alert" className="text-sm text-warn">{error}</p>}
      {series?.reason && <p className="text-sm text-fg-muted">{series.reason}</p>}
      {series && candles.length > 0 && (
        <>
          <ProChart candles={candles} symbol={title} timeframe="day" fullWindow drawingTools={false} defaultIndicators={series.ohlc ? ["volume"] : []} />
          <ContractPanel series={series} basisLink={basisLink} />
        </>
      )}
    </div>
  );
}
