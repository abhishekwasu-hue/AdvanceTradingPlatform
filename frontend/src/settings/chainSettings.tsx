/**
 * B4 (UI-H bug lane): his own Option chain settings (backend prefs namespace `option_chain`, app/me/prefs.py
 * OptionChainPrefs) - today the four PCR zone edges that word the "PCR zone" line of "What the chain shows" on the
 * Option chain page. The defaults are the values used before (0.70 / 0.90 / 1.00 / 1.30). Shown on Settings › My
 * trading defaults under "Option chain settings". Display words only: nothing trades on them.
 */
import { useEffect, useState } from "react";
import { request } from "../api/client";
import { CardV2 } from "../dashboard/CardV2";
import { BUTTON_PRIMARY, FIELD, NoteV2 } from "../trading/PartsV2";

export const CHAIN_NAMESPACE = "option_chain";
export const PCR_KEYS = ["very_low_below", "low_below", "balanced_max", "high_max"] as const;
export type PcrKey = (typeof PCR_KEYS)[number];
export type PcrZones = Record<PcrKey, number>;
export const PCR_DEFAULTS: PcrZones = { very_low_below: 0.7, low_below: 0.9, balanced_max: 1.0, high_max: 1.3 };
export const PCR_LABELS: Record<PcrKey, string> = {
  very_low_below: "Very low: under",
  low_below: "Low: under",
  balanced_max: "Balanced: up to",
  high_max: "High: up to (above it: very high)",
};
const MAX_EDGE = 10;

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

/** P1 (27 Oct): on an expiry day the chain and the spread builder open the next expiry (default ON). */
export function readExpiryDayNext(raw: unknown): boolean {
  return !(raw && typeof raw === "object" && (raw as { expiry_day_next?: unknown }).expiry_day_next === false);
}

export function readPcrZones(raw: unknown): PcrZones {
  const zones = raw && typeof raw === "object" ? (raw as { pcr_zones?: unknown }).pcr_zones : undefined;
  const r = (zones && typeof zones === "object" ? zones : {}) as Record<string, unknown>;
  const out = { ...PCR_DEFAULTS };
  for (const k of PCR_KEYS) if (num(r[k])) out[k] = r[k] as number;
  return out;
}

export type PcrForm = Record<PcrKey, string>;
export const pcrFormOf = (z: PcrZones): PcrForm =>
  Object.fromEntries(PCR_KEYS.map((k) => [k, z[k].toFixed(2)])) as PcrForm;

/** The fields as edges, with a plain sentence for the first value that cannot be saved. */
export function pcrOf(f: PcrForm): { value: PcrZones; problem: string | null } {
  const value = { ...PCR_DEFAULTS };
  for (const k of PCR_KEYS) {
    const v = Number(f[k].replace(/,/g, "").trim());
    if (!f[k].trim() || !Number.isFinite(v) || v <= 0 || v > MAX_EDGE) {
      return { value, problem: `Each PCR edge must be a number above 0 and up to ${MAX_EDGE}.` };
    }
    value[k] = v;
  }
  const edges = PCR_KEYS.map((k) => value[k]);
  if (edges.some((e, i) => i > 0 && e <= edges[i - 1])) {
    return { value, problem: "The PCR edges must go up from very low to high." };
  }
  return { value, problem: null };
}

interface PrefsRead { prefs?: Record<string, unknown>; versions?: Record<string, number> }

export default function ChainSettingsV2({ fetcher = request }: { fetcher?: typeof request }) {
  const [form, setForm] = useState<PcrForm | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [note, setNote] = useState<{ kind: "done" | "warn"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [nextOnExpiry, setNextOnExpiry] = useState(true);
  useEffect(() => {
    let live = true;
    fetcher<PrefsRead>("/me/prefs").then((r) => {
      if (!live) return;
      setForm(pcrFormOf(readPcrZones(r.prefs?.[CHAIN_NAMESPACE])));
      setNextOnExpiry(readExpiryDayNext(r.prefs?.[CHAIN_NAMESPACE]));
      setVersion(r.versions?.[CHAIN_NAMESPACE] ?? 0);
    }).catch(() => { if (live) setForm(pcrFormOf(PCR_DEFAULTS)); });
    return () => { live = false; };
  }, [fetcher]);
  if (!form) return null;

  async function save(f: PcrForm) {
    const { value, problem } = pcrOf(f);
    if (problem) { setNote({ kind: "warn", text: problem }); return; }
    setBusy(true);
    try {
      const q = version === null ? "" : `?version=${version}`;
      const r = await fetcher<{ value: unknown; version: number }>(`/me/prefs/${CHAIN_NAMESPACE}${q}`, { method: "PUT", body: JSON.stringify({ pcr_zones: value, expiry_day_next: nextOnExpiry }) });
      setForm(pcrFormOf(readPcrZones(r.value)));
      setNextOnExpiry(readExpiryDayNext(r.value));
      setVersion(r.version);
      setNote({ kind: "done", text: "Saved." });
    } catch (e) {
      setNote({ kind: "warn", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <CardV2 title="Option chain settings" testId="chain-settings">
      <div className="space-y-space-3">
        <p className="text-t14 text-fg-muted">
          PCR zones: where the put-call ratio counts as very low, low, balanced, high or very high in &quot;What the chain shows&quot;.
        </p>
        <div className="grid gap-space-3 md:grid-cols-2">
          {PCR_KEYS.map((k) => (
            <label key={k} className="space-y-space-1 text-t14 text-fg">
              <span>{PCR_LABELS[k]}</span>
              <input className={FIELD} inputMode="decimal" value={form[k]} data-chain-field={k}
                onChange={(e) => { setForm({ ...form, [k]: e.target.value }); setNote(null); }} />
            </label>
          ))}
        </div>
        <label className="flex min-h-11 cursor-pointer items-start gap-space-2 text-t14 text-fg">
          <input type="checkbox" className="mt-1 h-4 w-4 shrink-0 accent-brand" data-chain-field="expiry_day_next" checked={nextOnExpiry}
            onChange={(e) => { setNextOnExpiry(e.target.checked); setNote(null); }} />
          <span>On an expiry day, open the chain and the spread builder on the next expiry. Today&apos;s expiry stays one tap away.</span>
        </label>
        {note && <NoteV2 kind={note.kind} testId="chain-settings">{note.text}</NoteV2>}
        <div className="flex flex-wrap gap-space-2">
          <button type="button" className={BUTTON_PRIMARY} disabled={busy} onClick={() => void save(form)} data-chain-field="save">
            {busy ? "Saving…" : "Save"}
          </button>
          <button type="button" className="inline-flex h-11 items-center rounded-control px-space-3 text-t14 font-semibold text-brand hover:bg-surface-2 md:h-9"
            disabled={busy} onClick={() => { setForm(pcrFormOf(PCR_DEFAULTS)); setNote(null); }} data-chain-field="defaults">
            Use the standard values
          </button>
        </div>
      </div>
    </CardV2>
  );
}
