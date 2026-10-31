/**
 * N4 CH4c: an underlying's futures basis, cost of carry, calendar spread and rollover in its own tab (`/?basis=<SYM>`,
 * opened from a futures contract chart). Daily, from the shared history; every number is the server's. Reference only:
 * there is no order control here (flags `chart_futures_basis` + `chart_contracts`; off -> says so).
 */
import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import { errorSentence } from "../api/errors";
import { useAuth } from "../auth/AuthContext";
import { basisApi, latest, linePaths, pctText, type BasisSeries } from "../charting/futuresBasis";
import { SkeletonRows } from "../components/primitives";
import { formatNumber } from "../ui/format";

const W = 360;
const H = 56;

/** One series as a small line with its last value; the roll days are marked. */
export function BasisLine({ label, values, days, rolls, unit }: { label: string; values: (number | null)[]; days: string[]; rolls: string[]; unit: "pct" | "price" }) {
  const paths = linePaths(values, W, H);
  const last = values.length ? values[values.length - 1] : null;
  const text = unit === "pct" ? pctText(last) : last == null ? "n/a" : formatNumber(last, 2, { trim: true });
  const step = days.length > 1 ? W / (days.length - 1) : 0;
  return (
    <figure className="flex flex-col gap-1" data-testid="basis-line">
      <figcaption className="text-xs text-fg-muted"><span className="text-fg">{label}</span> {text}</figcaption>
      <svg width={W} height={H} role="img" aria-label={`${label} over ${days.length} days, last ${text}`}>
        {rolls.map((d) => {
          const i = days.indexOf(d);
          return i < 0 ? null : <line key={d} x1={i * step} x2={i * step} y1={0} y2={H} stroke="rgb(var(--chart-text))" opacity={0.25} strokeDasharray="2 3" />;
        })}
        {paths.map((p) => <polyline key={p} points={p} fill="none" stroke="rgb(var(--chart-text))" strokeWidth={1.5} />)}
      </svg>
    </figure>
  );
}

export function BasisPanel({ series }: { series: BasisSeries }) {
  const now = latest(series);
  return (
    <div className="flex flex-col gap-3" data-testid="basis-panel">
      {now && (
        <p className="text-sm text-fg" data-testid="basis-latest">
          {now.day}: near month {now.near ?? "n/a"} ({now.dte} days to expiry) · basis {pctText(now.basisPct)} · carry {pctText(now.carryPct)} a year
          · rollover {pctText(now.rolloverPct, 1)}
        </p>
      )}
      <BasisLine label="Basis (near - spot) %" values={series.basis_pct} days={series.days} rolls={series.rolls} unit="pct" />
      <BasisLine label="Cost of carry, annualised %" values={series.carry_pct} days={series.days} rolls={series.rolls} unit="pct" />
      <BasisLine label="Calendar spread (next - near)" values={series.calendar} days={series.days} rolls={series.rolls} unit="price" />
      <BasisLine label="Rollover (next + far OI, % of all)" values={series.rollover_pct} days={series.days} rolls={series.rolls} unit="pct" />
      <p className="text-xs text-fg-muted">
        Dashed lines: the days the near month rolled to the next expiry. Spot: {series.spot_key}. Sources: {series.sources.join(", ") || "none"}.
        {" "}{series.note}
      </p>
    </div>
  );
}

export default function FuturesBasisWindow({ params }: { params: URLSearchParams }) {
  const { user, loading } = useAuth();
  const underlying = (params.get("basis") ?? "").trim().toUpperCase();
  const [series, setSeries] = useState<BasisSeries | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { document.title = `${underlying} futures basis · AiAlgoDesk`; }, [underlying]);
  useEffect(() => {
    if (!user || !underlying) return;
    let live = true;
    basisApi.basis(underlying).then(
      (s) => { if (live) setSeries(s); },
      (e: unknown) => { if (live) setError(e instanceof ApiError && e.status === 503 ? "Futures basis charts are not enabled for your organisation." : errorSentence(e)); },
    );
    return () => { live = false; };
  }, [user, underlying]);

  if (loading) return <SkeletonRows rows={2} label="Loading" />;
  if (!user) return <div className="p-6 text-sm text-fg">Log in in the main tab first, then open the chart again.</div>;
  if (!underlying) return <div className="p-6 text-sm text-fg">No underlying given.</div>;
  return (
    <div className="min-h-screen bg-surface p-4">
      <h1 className="mb-2 text-sm font-semibold text-fg">{underlying} futures: basis, carry and rollover</h1>
      {error && <p role="alert" className="text-sm text-warn">{error}</p>}
      {series?.reason && <p className="text-sm text-fg-muted">{series.reason}</p>}
      {series && series.days.length > 0 && <BasisPanel series={series} />}
    </div>
  );
}
