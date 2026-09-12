export default function EquityCurveChart({ equity }: { equity: number[] }) {
  if (equity.length < 2) {
    return <div className="text-sm text-muted py-8 text-center">Not enough trades to plot an equity curve.</div>;
  }

  const width = 720;
  const height = 220;
  const padding = 24;

  const min = Math.min(...equity);
  const max = Math.max(...equity);
  const range = max - min || 1;

  const points = equity.map((value, i) => {
    const x = padding + (i / (equity.length - 1)) * (width - padding * 2);
    const y = height - padding - ((value - min) / range) * (height - padding * 2);
    return [x, y] as const;
  });

  const path = points.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const finalUp = equity[equity.length - 1] >= equity[0];
  const lineColor = finalUp ? "#22c55e" : "#ef4444";
  const areaPath = `${path} L${points[points.length - 1][0]},${height - padding} L${points[0][0]},${height - padding} Z`;

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="w-full h-auto">
      <defs>
        <linearGradient id="equityFill" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={lineColor} stopOpacity="0.25" />
          <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={areaPath} fill="url(#equityFill)" stroke="none" />
      <path d={path} fill="none" stroke={lineColor} strokeWidth={2} />
      <text x={padding} y={16} className="fill-slate-400" fontSize="11">
        {min.toFixed(0)} – {max.toFixed(0)}
      </text>
    </svg>
  );
}
