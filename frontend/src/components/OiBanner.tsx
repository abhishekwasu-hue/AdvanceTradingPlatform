import { useEffect, useState } from "react";
import { api } from "../api/client";
import { bannerLines } from "../oi/format";
import type { OiBannerResponse } from "../oi/types";

/** OI Banner (O3): the presentational banner. The direction colours its left border; the lines are the headline,
 *  "Put: … · Call: …", "PCR … — band", then chips (DTE, max pain, stale / market closed, data time). Read-only. */
export function OiBannerView({ data, compact = false }: { data: OiBannerResponse; compact?: boolean }) {
  const lines = bannerLines(data);
  return (
    <div className={`rounded-lg border border-border border-l-4 ${lines.tone.border} bg-surface-2 ${compact ? "px-3 py-2" : "px-4 py-3"}`}
         role="status" aria-label={`${data.underlying} OI banner: ${lines.tone.label}`}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-xs font-semibold text-fg-muted">{data.underlying} · OI</span>
        <span className={`text-[11px] font-bold uppercase ${lines.tone.text}`}>{lines.tone.label}</span>
      </div>
      <div className={`mt-1 font-semibold ${compact ? "text-sm" : "text-base"} ${lines.tone.text}`}>{lines.headline}</div>
      {lines.classes && <div className="mt-1 text-xs text-fg">{lines.classes}</div>}
      {lines.pcr && <div className="mt-0.5 text-xs text-fg-muted">{lines.pcr}</div>}
      {lines.chips.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {lines.chips.map((c) => (
            <span key={c} className={`rounded-full border px-2 py-0.5 text-[11px] ${c.startsWith("Stale") ? "border-warn text-warn" : "border-border text-fg-muted"}`}>{c}</span>
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
  if (error) return <div className="text-xs text-down">OI banner unavailable: {error}</div>;
  if (!data) return <div className="h-16 animate-pulse rounded-lg bg-surface-2" aria-label="Loading OI banner" />;
  return <OiBannerView data={data} compact={compact} />;
}
