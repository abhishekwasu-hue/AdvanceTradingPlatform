import { cx } from "../components/primitives/cx";
import { formatTimeIST } from "../ui/format";
import {
  CHART, formatCell, formatStrike, xPixel, yPixel, yScale, type AnalysisResult, type Evaluated, type ResultChart,
} from "./askAi";

/**
 * OC3: one evaluated analysis - the table (strike first, then the spec's columns), the optional small chart, the
 * "How this was calculated" disclosure (the spec in plain words, written by the server from the spec itself) and the
 * educational disclaimer. Pure: it only shows what the server computed; nothing here makes a number.
 */
export function AnalysisResultView({ evaluated, disclaimer, asOf }: { evaluated: Evaluated; disclaimer: string; asOf?: string | null }) {
  const { spec, description, result } = evaluated;
  return (
    <div className="space-y-space-3" data-oc-ask-result={result.state}>
      <div className="flex flex-wrap items-baseline justify-between gap-space-2">
        <h3 className="text-t14 font-semibold text-fg">{spec.title}</h3>
        <p className="text-t12 text-fg-muted">{provenance(result, asOf)}</p>
      </div>
      {result.message && <p className="text-t14 text-fg-muted">{result.message}</p>}
      {result.rows.length > 0 && result.chart && <AskChart chart={result.chart} title={spec.title} />}
      {result.rows.length > 0 && <ResultTable result={result} />}
      <details className="rounded-panel bg-surface-2 p-space-2" data-oc-ask-how>
        <summary className="min-h-11 cursor-pointer content-center text-t13 font-semibold text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand">
          How this was calculated
        </summary>
        <ul className="mt-space-1 list-disc space-y-space-1 pl-space-4 text-t13 text-fg">
          {description.map((line, i) => <li key={i}>{line}</li>)}
          {result.history.filter((h) => h.at).map((h) => (
            <li key={h.ref}>{`Earlier values (${h.ref === "open" ? "session start" : h.ref.replace("-", "").replace("m", " minutes ago")}) were read from the snapshot stored at ${formatTimeIST(h.at)}.`}</li>
          ))}
          <li>Every number in the table is computed by the server from your broker&apos;s option chain; the AI only wrote the analysis.</li>
        </ul>
      </details>
      <p className="text-t12 text-fg-muted" data-oc-ask-disclaimer>{disclaimer}</p>
    </div>
  );
}

export function provenance(result: AnalysisResult, asOf?: string | null): string {
  const parts = [];
  if (result.atm_strike !== null) parts.push(`ATM ${formatStrike(result.atm_strike)}`);
  if (result.spot !== null) parts.push(`spot ${formatCell(result.spot)}`);
  parts.push(`${result.rows_kept} of ${result.rows_in_window} strikes`);
  if (asOf) parts.push(`data as of ${formatTimeIST(asOf)}`);
  return parts.join(" · ");
}

export function ResultTable({ result }: { result: AnalysisResult }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-max border-collapse text-t13">
        <thead>
          <tr className="border-b border-subtle">
            {result.columns.map((c, i) => (
              <th key={c.key} scope="col" className={cx("px-space-2 py-space-1 font-normal text-fg-muted", i === 0 ? "text-left" : "text-right")}>{c.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.map((r) => (
            <tr key={r.strike} className={cx("border-b border-subtle last:border-0", r.atm_offset === 0 && "bg-surface-2 font-semibold")}>
              <th scope="row" className="px-space-2 py-space-1 text-left font-tabular text-fg">
                {formatStrike(r.strike)}{r.atm_offset === 0 && <span className="ml-space-1 text-t12 font-normal text-fg-muted">ATM</span>}
              </th>
              {r.cells.map((v, i) => <td key={i} className="px-space-2 py-space-1 text-right font-tabular text-fg">{formatCell(v)}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const TONES = [{ fill: "fill-brand", stroke: "stroke-brand", swatch: "bg-brand" }, { fill: "fill-info", stroke: "stroke-info", swatch: "bg-info" }];

/** A small plain-SVG chart (bars or lines) in v2 tokens; a missing value is a gap, never drawn as 0. */
export function AskChart({ chart, title }: { chart: ResultChart; title: string }) {
  const scale = yScale(chart);
  const xs = chart.series[0]?.points.map((p) => p.x) ?? [];
  if (!scale || !xs.length) return null;
  const { width, height, padLeft, padRight } = CHART;
  const n = xs.length;
  const slot = (width - padLeft - padRight) / n;
  const zero = yPixel(Math.max(scale.min, Math.min(0, scale.max)), scale);
  const barWidth = Math.max(1, (slot * 0.8) / Math.max(chart.series.length, 1));
  const labelEvery = Math.max(1, Math.ceil(n / 6));
  const ticks = [scale.max, (scale.max + scale.min) / 2, scale.min];
  return (
    <figure className="space-y-space-1" data-chart={chart.type}>
      <svg viewBox={`0 0 ${width} ${height}`} className="h-40 w-full" role="img"
        aria-label={`${title}: ${chart.series.map((s) => s.label).join(" and ")} by ${chart.x_label.toLowerCase()}`}>
        {ticks.map((t, i) => (
          <g key={i}>
            <line x1={padLeft} x2={width - padRight} y1={yPixel(t, scale)} y2={yPixel(t, scale)} className="stroke-border-subtle" strokeWidth={1} />
            <text x={padLeft - 4} y={yPixel(t, scale) + 3} textAnchor="end" className="fill-fg-muted text-xs">{formatCell(t)}</text>
          </g>
        ))}
        {chart.type === "bar" && <line x1={padLeft} x2={width - padRight} y1={zero} y2={zero} className="stroke-fg-muted" strokeWidth={1} />}
        {chart.type === "bar" && chart.series.map((s, si) => s.points.map((p, i) => {
          if (p.y === null) return null;
          const x = xPixel(i, n) - (slot * 0.8) / 2 + si * barWidth;
          const y = yPixel(p.y, scale);
          return (
            <rect key={`${si}-${i}`} x={x} y={Math.min(y, zero)} width={Math.max(1, barWidth - 1)} height={Math.max(0.5, Math.abs(zero - y))}
              className={TONES[si % TONES.length].fill}>
              <title>{`${s.label} at ${formatStrike(p.x)}: ${formatCell(p.y)}`}</title>
            </rect>
          );
        }))}
        {chart.type === "line" && chart.series.map((s, si) => {
          const runs: string[][] = [[]];
          s.points.forEach((p, i) => {
            if (p.y === null) { runs.push([]); return; }
            runs[runs.length - 1].push(`${xPixel(i, n).toFixed(1)},${yPixel(p.y, scale).toFixed(1)}`);
          });
          return (
            <g key={si}>
              {runs.filter((r) => r.length > 1).map((r, ri) => (
                <polyline key={ri} points={r.join(" ")} fill="none" className={TONES[si % TONES.length].stroke} strokeWidth={1.5} />
              ))}
              {s.points.map((p, i) => p.y === null ? null : (
                <circle key={i} cx={xPixel(i, n)} cy={yPixel(p.y, scale)} r={2} className={cx(TONES[si % TONES.length].stroke, TONES[si % TONES.length].fill)}>
                  <title>{`${s.label} at ${formatStrike(p.x)}: ${formatCell(p.y)}`}</title>
                </circle>
              ))}
            </g>
          );
        })}
        {xs.map((x, i) => (i % labelEvery === 0 || i === n - 1) && (
          <text key={i} x={xPixel(i, n)} y={height - 6} textAnchor="middle" className="fill-fg-muted text-xs">{formatStrike(x)}</text>
        ))}
      </svg>
      <figcaption className="flex flex-wrap gap-x-space-3 gap-y-space-1 text-t12 text-fg-muted">
        {chart.series.map((s, si) => (
          <span key={si} className="inline-flex items-center gap-space-1">
            <span aria-hidden className={cx("inline-block h-2 w-3 rounded-sm", TONES[si % TONES.length].swatch)} />{s.label}
          </span>
        ))}
        <span>by {chart.x_label.toLowerCase()}</span>
      </figcaption>
    </figure>
  );
}
