import { useEffect, useState } from "react";
import { api } from "../api/client";
import { formatOi, formatSigned, slotTime, tone } from "../oi/format";
import type { OiHistoryResponse } from "../oi/types";

const INTERVALS = [5, 10, 15] as const;

/** OI Banner (O3): the day's banner history, newest first, in 5/10/15-minute views (last value per bucket). */
export default function OiHistoryTable({ underlying, date }: { underlying: string; date?: string }) {
  const [interval, setIntervalMinutes] = useState<(typeof INTERVALS)[number]>(5);
  const [data, setData] = useState<OiHistoryResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api.oiHistory(underlying, interval, date).then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e) => { if (alive) setError(String(e?.message ?? e)); });
    return () => { alive = false; };
  }, [underlying, interval, date]);
  return (
    <div>
      <div className="mb-2 flex items-center gap-2 text-xs">
        <span className="text-fg-muted">View</span>
        {INTERVALS.map((m) => (
          <button key={m} onClick={() => setIntervalMinutes(m)}
                  className={`rounded px-2 py-0.5 border ${interval === m ? "border-brand text-fg" : "border-border text-fg-muted"}`}>{m} min</button>
        ))}
      </div>
      {error && <div className="text-xs text-down">{error}</div>}
      {data && data.rows.length === 0 && <div className="text-xs text-fg-muted">No snapshots for {data.date} yet.</div>}
      {data && data.rows.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full text-xs tabular-nums">
            <thead className="text-fg-muted">
              <tr className="text-left">
                <th className="py-1 pr-3">Time</th><th className="pr-3">Call OI</th><th className="pr-3">Put OI</th><th className="pr-3">Diff</th>
                <th className="pr-3">Δ Diff</th><th className="pr-3">Signal</th><th className="pr-3">PCR</th><th>Banner</th>
              </tr>
            </thead>
            <tbody>
              {data.rows.map((r) => (
                <tr key={r.slot} className="border-t border-border">
                  <td className="py-1 pr-3">{slotTime(r.slot)}</td>
                  <td className="pr-3">{formatOi(r.total_call_oi)}</td>
                  <td className="pr-3">{formatOi(r.total_put_oi)}</td>
                  <td className="pr-3">{formatSigned(r.diff)}</td>
                  <td className="pr-3">{formatSigned(r.delta_diff)}</td>
                  <td className="pr-3">{r.signal}</td>
                  <td className="pr-3">{r.pcr === null ? "—" : r.pcr.toFixed(2)}</td>
                  <td className={tone(r.direction).text}>{tone(r.direction).label}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
