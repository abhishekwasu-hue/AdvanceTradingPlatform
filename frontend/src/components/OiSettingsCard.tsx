import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { OiSettingsResponse } from "../oi/types";

const FIELDS: { key: string; label: string; step: string }[] = [
  { key: "strike_step", label: "Strike step (blank = from the chain)", step: "any" },
  { key: "atm_range", label: "Strikes either side of ATM", step: "1" },
  { key: "confirm_count", label: "Snapshots to confirm a flip", step: "1" },
  { key: "oi_threshold_pct", label: "OI change threshold %", step: "0.1" },
  { key: "premium_threshold_pct", label: "Premium change threshold %", step: "0.1" },
  { key: "stale_after_minutes", label: "Stale after (minutes)", step: "1" },
];

/** OI Banner (O3): follow an underlying and tune the main thresholds (organisation owner; the server validates). */
export default function OiSettingsCard({ underlying }: { underlying: string }) {
  const [data, setData] = useState<OiSettingsResponse | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    api.oiSettings(underlying).then((d) => { setData(d); setDraft({}); setNote(null); }).catch((e) => setNote(String(e?.message ?? e)));
  }, [underlying]);
  if (!data) return note ? <div className="text-xs text-down">{note}</div> : null;

  async function save(enabled?: boolean) {
    if (!data) return;
    const overrides: Record<string, unknown> = { ...data.overrides };
    for (const [k, v] of Object.entries(draft)) {
      if (v.trim() === "") delete overrides[k];
      else overrides[k] = Number(v);
    }
    try {
      const next = await api.saveOiSettings(underlying, { enabled: enabled ?? data.enabled, overrides });
      setData(next); setDraft({}); setNote("Saved.");
    } catch (e) {
      setNote(String((e as Error)?.message ?? e));
    }
  }

  return (
    <div className="space-y-2 text-xs">
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={data.enabled} onChange={(e) => save(e.target.checked)} />
        <span>Collect OI snapshots for {underlying} (every few minutes while the market is open)</span>
      </label>
      <div className="grid gap-2 sm:grid-cols-3">
        {FIELDS.map((f) => (
          <label key={f.key} className="block">
            <span className="text-fg-muted">{f.label}</span>
            <input type="number" step={f.step} className="mt-0.5 w-full rounded border border-border bg-surface-2 px-2 py-1"
                   value={draft[f.key] ?? String(data.overrides[f.key] ?? "")}
                   placeholder={String(data.effective[f.key] ?? "")}
                   onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })} />
          </label>
        ))}
      </div>
      <div className="flex items-center gap-3">
        <button onClick={() => save()} className="rounded border border-border px-3 py-1 hover:border-brand">Save thresholds</button>
        {note && <span className="text-fg-muted">{note}</span>}
      </div>
    </div>
  );
}
