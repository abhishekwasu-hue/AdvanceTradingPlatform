/** Cumulative R after each simulated trade - a flat line chart (readability first) inside a depth card. The split
 * between the tuning and the unseen sessions is marked when known. */
export function cumulative(rs: number[]): number[] {
  const out = [0];
  for (const r of rs) out.push(Number((out[out.length - 1] + r).toFixed(4)));
  return out;
}

export default function EquityCurve({ trades, splitAt, label }: { trades: { r: number }[]; splitAt?: number | null; label: string }) {
  const series = cumulative(trades.map((t) => t.r));
  const W = 320, H = 96, P = 6;
  const min = Math.min(...series), max = Math.max(...series);
  const span = max - min || 1;
  const x = (i: number) => P + (i / Math.max(1, series.length - 1)) * (W - 2 * P);
  const y = (v: number) => H - P - ((v - min) / span) * (H - 2 * P);
  const d = series.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = series[series.length - 1];
  const stroke = last > 0 ? "rgb(var(--up))" : last < 0 ? "rgb(var(--down))" : "rgb(var(--fg-muted))";
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="h-24 w-full" role="img" aria-label={label}>
      <line x1={P} x2={W - P} y1={y(0)} y2={y(0)} stroke="rgb(var(--border))" strokeDasharray="3 4" />
      {splitAt != null && splitAt > 0 && splitAt < series.length - 1 && (
        <line x1={x(splitAt)} x2={x(splitAt)} y1={P} y2={H - P} stroke="rgb(var(--ai))" strokeOpacity="0.6" strokeDasharray="2 3" />
      )}
      <path d={d} fill="none" stroke={stroke} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}
