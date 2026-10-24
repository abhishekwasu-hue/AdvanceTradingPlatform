/**
 * Terminal master Part 7 (flag smart_execution): his own numbers for the smart-execution rules (backend prefs namespace
 * `smart_execution`, app/me/prefs.py SmartExecutionPrefs) - when the delivery warning for stock F&O starts and how near
 * the money counts, whether it also goes to Telegram, and the protection band of a marketable limit where a broker takes
 * no market order. Shown on Settings › My trading defaults. Information and order style only: never holds back an order.
 */
import { useEffect, useState } from "react";
import { request } from "../api/client";
import { CardV2 } from "../dashboard/CardV2";
import { BUTTON_PRIMARY, FIELD, NoteV2 } from "../trading/PartsV2";

export const SMART_NAMESPACE = "smart_execution";
export interface SmartSettings { settlement_days_before: number; settlement_band_pct: number; settlement_telegram: boolean; protection_band_pct: number }
export const SMART_DEFAULTS: SmartSettings = { settlement_days_before: 4, settlement_band_pct: 2, settlement_telegram: true, protection_band_pct: 0.5 };
export const BOUNDS = { settlement_days_before: [0, 30], settlement_band_pct: [0, 20], protection_band_pct: [0.05, 5] } as const;

const num = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v);

export function readSmart(raw: unknown): SmartSettings {
  const r = (raw && typeof raw === "object" ? raw : {}) as Record<string, unknown>;
  return {
    settlement_days_before: num(r.settlement_days_before) ? r.settlement_days_before : SMART_DEFAULTS.settlement_days_before,
    settlement_band_pct: num(r.settlement_band_pct) ? r.settlement_band_pct : SMART_DEFAULTS.settlement_band_pct,
    settlement_telegram: typeof r.settlement_telegram === "boolean" ? r.settlement_telegram : SMART_DEFAULTS.settlement_telegram,
    protection_band_pct: num(r.protection_band_pct) ? r.protection_band_pct : SMART_DEFAULTS.protection_band_pct,
  };
}

export interface SmartForm { settlement_days_before: string; settlement_band_pct: string; settlement_telegram: boolean; protection_band_pct: string }
export const smartFormOf = (s: SmartSettings): SmartForm => ({
  settlement_days_before: String(s.settlement_days_before), settlement_band_pct: String(s.settlement_band_pct),
  settlement_telegram: s.settlement_telegram, protection_band_pct: String(s.protection_band_pct),
});

/** The fields as settings, with a plain sentence per value that cannot be saved. */
export function smartOf(f: SmartForm): { value: SmartSettings; problems: string[] } {
  const problems: string[] = [];
  const read = (text: string, key: keyof typeof BOUNDS, label: string, whole = false): number => {
    const v = Number(text.replace(/,/g, "").trim());
    const [lo, hi] = BOUNDS[key];
    if (!text.trim() || !Number.isFinite(v) || v < lo || v > hi || (whole && !Number.isInteger(v))) {
      problems.push(`${label} must be ${whole ? "a whole number " : ""}from ${lo} to ${hi}.`);
      return SMART_DEFAULTS[key];
    }
    return v;
  };
  const value: SmartSettings = {
    settlement_days_before: read(f.settlement_days_before, "settlement_days_before", "Days before expiry", true),
    settlement_band_pct: read(f.settlement_band_pct, "settlement_band_pct", "Near the money"),
    settlement_telegram: f.settlement_telegram,
    protection_band_pct: read(f.protection_band_pct, "protection_band_pct", "Price band"),
  };
  return { value, problems };
}

interface PrefsRead { prefs?: Record<string, unknown>; versions?: Record<string, number> }

export default function SmartExecutionSettingsV2({ fetcher = request }: { fetcher?: typeof request }) {
  const [form, setForm] = useState<SmartForm | null>(null);
  const [version, setVersion] = useState<number | null>(null);
  const [note, setNote] = useState<{ kind: "done" | "warn"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    fetcher<PrefsRead>("/me/prefs").then((r) => {
      if (!live) return;
      setForm(smartFormOf(readSmart(r.prefs?.[SMART_NAMESPACE])));
      setVersion(r.versions?.[SMART_NAMESPACE] ?? 0);
    }).catch(() => { if (live) setForm(smartFormOf(SMART_DEFAULTS)); });
    return () => { live = false; };
  }, [fetcher]);
  if (!form) return null;

  async function save(f: SmartForm) {
    const { value, problems } = smartOf(f);
    if (problems.length) { setNote({ kind: "warn", text: problems[0] }); return; }
    setBusy(true);
    try {
      const q = version === null ? "" : `?version=${version}`;
      const r = await fetcher<{ value: unknown; version: number }>(`/me/prefs/${SMART_NAMESPACE}${q}`, { method: "PUT", body: JSON.stringify(value) });
      setForm(smartFormOf(readSmart(r.value)));
      setVersion(r.version);
      setNote({ kind: "done", text: "Saved." });
    } catch (e) {
      setNote({ kind: "warn", text: e instanceof Error ? e.message : String(e) });
    } finally {
      setBusy(false);
    }
  }

  const set = (patch: Partial<SmartForm>) => { setForm({ ...form, ...patch }); setNote(null); };
  return (
    <CardV2 title="Multi-leg orders and expiry" testId="smart-execution">
      <div className="space-y-space-3">
        <p className="text-t13 text-fg-muted">
          A stock F&amp;O position in or near the money, held to expiry, settles by delivery. You get a warning on Positions this many days ahead.
        </p>
        <div className="grid gap-space-3 md:grid-cols-2">
          <label className="space-y-space-1 text-t14 text-fg">
            <span>Days before expiry</span>
            <input className={FIELD} inputMode="numeric" value={form.settlement_days_before} data-smart-field="days"
              onChange={(e) => set({ settlement_days_before: e.target.value })} />
          </label>
          <label className="space-y-space-1 text-t14 text-fg">
            <span>Near the money (% of the strike)</span>
            <input className={FIELD} inputMode="decimal" value={form.settlement_band_pct} data-smart-field="band"
              onChange={(e) => set({ settlement_band_pct: e.target.value })} />
          </label>
          <label className="flex min-h-11 items-center gap-space-2 text-t14 text-fg md:col-span-2">
            <input type="checkbox" checked={form.settlement_telegram} data-smart-field="telegram"
              onChange={(e) => set({ settlement_telegram: e.target.checked })} />
            Also send the warning to my Telegram
          </label>
          <label className="space-y-space-1 text-t14 text-fg md:col-span-2">
            <span>Price band where a broker takes no market order (%)</span>
            <input className={FIELD} inputMode="decimal" value={form.protection_band_pct} data-smart-field="protection"
              onChange={(e) => set({ protection_band_pct: e.target.value })} />
          </label>
        </div>
        {note && <NoteV2 kind={note.kind} testId="smart-execution">{note.text}</NoteV2>}
        <button type="button" className={BUTTON_PRIMARY} disabled={busy} onClick={() => void save(form)} data-smart-field="save">
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </CardV2>
  );
}
