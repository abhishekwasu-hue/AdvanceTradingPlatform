import { useEffect, useState } from "react";
import { api } from "../api/client";
import { formatOi, formatSigned, slotTime, strikeBars } from "../oi/format";
import type { OiStrikesResponse } from "../oi/types";

/** OI Banner (O3): call and put OI per strike in the banner's ATM window (latest slot), with the change since the
 *  day's first reading in each bar's tooltip. Plain SVG; the numbers come from the API, never from pixels. */
export default function StrikeOiChart({ underlying, date }: { underlying: string; date?: string }) {
  const [data, setData] = useState<OiStrikesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api.oiStrikes(underlying, date).then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e) => { if (alive) setError(String(e?.message ?? e)); });
    return () => { alive = false; };
  }, [underlying, date]);
  if (error) return <div className="text-xs text-down">{error}</div>;
  if (!data) return null;
  const bars = strikeBars(data.strikes);
  if (!bars.length) return <div className="text-xs text-fg-muted">No per-strike OI for {data.date} yet.</div>;
  const max = Math.max(1, ...bars.flatMap((b) => [b.call ?? 0, b.put ?? 0]));
  const w = 28, h = 140, gap = 6;
  return (
    <div className="overflow-x-auto">
      <svg width={bars.length * (w + gap)} height={h + 28} role="img" aria-label={`${underlying} OI by strike`}>
        {bars.map((b, i) => {
          const x = i * (w + gap);
          const ch = ((b.call ?? 0) / max) * h, ph = ((b.put ?? 0) / max) * h;
          return (
            <g key={b.strike}>
              <rect x={x} y={h - ch} width={w / 2 - 1} height={ch} className="fill-down/70">
                <title>{`${b.strike} CE: ${formatOi(b.call)} (${formatSigned(b.callChange)} today)`}</title>
              </rect>
              <rect x={x + w / 2} y={h - ph} width={w / 2 - 1} height={ph} className="fill-up/70">
                <title>{`${b.strike} PE: ${formatOi(b.put)} (${formatSigned(b.putChange)} today)`}</title>
              </rect>
              <text x={x + w / 2} y={h + 14} textAnchor="middle" className="fill-fg-muted text-[9px]">{b.strike}</text>
            </g>
          );
        })}
      </svg>
      <div className="mt-1 text-[11px] text-fg-muted">Calls (red) and puts (green) · data as of {slotTime(data.data_as_of)}</div>
    </div>
  );
}
