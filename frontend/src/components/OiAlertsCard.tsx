import { useEffect, useState } from "react";
import { api } from "../api/client";
import { OI_ALERT_TYPES, type OiAlertLogResponse, type OiAlertSettings, type OiSettingsResponse } from "../oi/types";
import { slotTime } from "../oi/format";

const LABEL: Record<string, string> = {
  DIRECTION_CHANGE: "Banner direction change", STABLE_FLIP: "Confirmed trend flip", STRENGTH_CHANGE: "Strong ↔ Weakening",
  PCR_BAND: "PCR band change", MAX_PAIN_MOVE: "Max pain move", OI_WALL: "OI wall formed / broken", DTE: "Expiry tomorrow / today",
  COLLECTOR_STALE: "Collector stale (ops)",
};

/** OI Banner (O4b): which banner changes notify, how often, quiet hours and the daily digest (owner saves; the server
 *  validates), plus snooze / mute / resume, a test alert and today's alert log. Alerts are information - never an order. */
export default function OiAlertsCard({ underlying }: { underlying: string }) {
  const [settings, setSettings] = useState<OiSettingsResponse | null>(null);
  const [log, setLog] = useState<OiAlertLogResponse | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const load = () => {
    api.oiSettings(underlying).then(setSettings).catch((e) => setNote(String(e?.message ?? e)));
    api.oiAlerts(underlying).then(setLog).catch(() => setLog(null));
  };
  useEffect(load, [underlying]);
  if (!settings) return note ? <div className="text-xs text-down">{note}</div> : null;
  const alerts = (settings.effective.alerts ?? {}) as OiAlertSettings;

  async function save(patch: Partial<OiAlertSettings>) {
    if (!settings) return;
    const current = (settings.overrides.alerts ?? {}) as Partial<OiAlertSettings>;
    try {
      const next = await api.saveOiSettings(underlying, { overrides: { ...settings.overrides, alerts: { ...current, ...patch } } });
      setSettings(next); setNote("Saved.");
    } catch (e) { setNote(String((e as Error)?.message ?? e)); }
  }
  async function act(action: "snooze" | "mute-today" | "resume" | "test") {
    try { await api.oiAlertAction(underlying, action); setNote(action === "test" ? "Test alert sent to your channels." : "Done."); load(); }
    catch (e) { setNote(String((e as Error)?.message ?? e)); }
  }
  const types = new Set(alerts.types ?? []);
  return (
    <div className="space-y-3 text-xs">
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={!!alerts.enabled} onChange={(e) => save({ enabled: e.target.checked })} />
        <span>Notify me when the {underlying} OI banner changes (through Settings → Alert channels)</span>
      </label>
      <div className="grid gap-1 sm:grid-cols-2">
        {OI_ALERT_TYPES.map((t) => (
          <label key={t} className="flex items-center gap-2">
            <input type="checkbox" checked={types.has(t)}
                   onChange={(e) => save({ types: e.target.checked ? [...types, t] : [...types].filter((x) => x !== t) })} />
            <span>{LABEL[t] ?? t}</span>
          </label>
        ))}
      </div>
      <div className="grid gap-2 sm:grid-cols-4">
        <Field label="Cooldown (minutes)" value={alerts.cooldown_minutes} onSave={(v) => save({ cooldown_minutes: Number(v) })} />
        <Field label="Max pain move (strikes)" value={alerts.max_pain_strikes} onSave={(v) => save({ max_pain_strikes: Number(v) })} />
        <Field label="Quiet from (IST HH:MM)" value={alerts.quiet_start ?? ""} onSave={(v) => save({ quiet_start: v || null })} />
        <Field label="Quiet until (IST HH:MM)" value={alerts.quiet_end ?? ""} onSave={(v) => save({ quiet_end: v || null })} />
        <Field label="Daily digest at (IST HH:MM)" value={alerts.digest_time ?? ""} onSave={(v) => save({ digest_time: v || null })} />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button className="rounded border border-border px-2 py-1 hover:border-brand" onClick={() => act("snooze")}>Snooze 1h</button>
        <button className="rounded border border-border px-2 py-1 hover:border-brand" onClick={() => act("mute-today")}>Mute today</button>
        <button className="rounded border border-border px-2 py-1 hover:border-brand" onClick={() => act("resume")}>Resume</button>
        <button className="rounded border border-border px-2 py-1 hover:border-brand" onClick={() => act("test")}>Send a test alert</button>
        {log?.snoozed_until && <span className="text-warn">Paused until {slotTime(log.snoozed_until)}</span>}
        {note && <span className="text-fg-muted">{note}</span>}
      </div>
      {log && log.alerts.length > 0 && (
        <ul className="space-y-0.5 tabular-nums">
          {log.alerts.slice(0, 20).map((a, i) => (
            <li key={i} className="text-fg-muted">
              {slotTime(a.slot)} · {LABEL[a.alert_type] ?? a.alert_type}: {a.old_state ?? "—"} → {a.new_state}
              <span className={a.status === "SENT" ? " text-fg" : " text-warn"}> · {a.status.toLowerCase()}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function Field({ label, value, onSave }: { label: string; value: string | number; onSave: (v: string) => void }) {
  const [draft, setDraft] = useState<string | null>(null);
  return (
    <label className="block">
      <span className="text-fg-muted">{label}</span>
      <input className="mt-0.5 w-full rounded border border-border bg-surface-2 px-2 py-1" value={draft ?? String(value ?? "")}
             onChange={(e) => setDraft(e.target.value)} onBlur={() => { if (draft !== null) { onSave(draft.trim()); setDraft(null); } }} />
    </label>
  );
}
