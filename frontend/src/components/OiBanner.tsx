import { useEffect, useState } from "react";
import { api } from "../api/client";
import { bannerLines, isLateChip } from "../oi/format";
import type { OiBannerResponse } from "../oi/types";
import { Skeleton } from "./primitives";

/** OI Banner (O3): the presentational banner. M61 OI-5: numbers and neutral words only, no direction colour - the
 *  lines are the read ("Put OI above call OI (PCR 1.04)"), each side's OI and premium move, the two totals, then chips
 *  (DTE, max pain, last update / market closed, data time). Read-only. */
export function OiBannerView({ data, compact = false }: { data: OiBannerResponse; compact?: boolean }) {
  const lines = bannerLines(data);
  return (
    <div className={`rounded-lg border border-border bg-surface-2 ${compact ? "px-3 py-2" : "px-4 py-3"}`}
         role="status" aria-label={`${data.underlying} OI summary`}>
      <div className="text-xs font-semibold text-fg-muted">{data.underlying} · OI</div>
      <div className={`mt-1 font-semibold tabular-nums text-fg ${compact ? "text-sm" : "text-base"}`}>{lines.headline}</div>
      {lines.classes && <div className="mt-1 text-xs text-fg">{lines.classes}</div>}
      {lines.totals && <div className="mt-0.5 text-xs tabular-nums text-fg-muted">{lines.totals}</div>}
      {lines.chips.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {lines.chips.map((c) => (
            <span key={c} className={`rounded-full border px-2 py-0.5 text-xs ${isLateChip(c) ? "border-warn text-warn" : "border-border text-fg-muted"}`}>{c}</span>
          ))}
        </div>
      )}
    </div>
  );
}

/** The banner for one underlying, refreshed every minute (the collector writes one slot every few minutes). */
export default function OiBanner({ underlying, compact = false }: { underlying: string; compact?: boolean }) {
  const [data, setData] = useState<OiBannerResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const load = () => api.oiBanner(underlying).then((d) => { if (alive) { setData(d); setError(null); } })
      .catch((e) => { if (alive) setError(String(e?.message ?? e)); });
    load();
    const id = window.setInterval(load, 60_000);
    return () => { alive = false; window.clearInterval(id); };
  }, [underlying]);
  if (error) return <div className="text-xs text-fg-muted">The OI summary is not available right now.</div>;
  if (!data) return <Skeleton className="h-16" panel label="Loading OI banner" />;
  return <OiBannerView data={data} compact={compact} />;
}
