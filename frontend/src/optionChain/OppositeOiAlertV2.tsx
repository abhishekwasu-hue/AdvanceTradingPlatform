import { Bell, BellOff } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { errorSentence } from "../api/errors";
import { cx } from "../components/primitives/cx";
import { Card } from "../components/ui";
import type { OiAlertSettings, OiSettingsResponse } from "../oi/types";
import { formatNumber } from "../ui/format";
import { withType } from "./OiThresholdAlertsV2";

/**
 * Owner order 13 Nov: "Alert me on opposite OI moves" - one bell per symbol beside the OI history (the chain view) and in
 * Settings → Alerts. ON only for the symbols he turns on here (owner answer 13 Nov: nothing automatic); OFF by default.
 * The rule (server, app/option_chain/oi_opposite.py): the total call OI and total put OI over ATM ± 6 move in opposite
 * directions in one window (5 / 10 / 15 min), each side by at least the % (Trade's rule) and by at least k × its average
 * of the last M windows (owner answer: "sudden" against the recent windows). Both up or both down: no alert. The alert
 * goes through his usual channels (bell, browser push, Telegram) with the same cooldown, quiet hours and snooze.
 * Every number is his own; the shown defaults are references to edit before use.
 */
export const OPPOSITE = "OPPOSITE_OI";

export const REFERENCE = { window: 5, pct: 2, k: 2, m: 6 } as const;

/** Is the bell on for this symbol: OPPOSITE_OI in THIS symbol's own alert types (the server's `bell_on`). Pure. */
export function bellOn(s: OiSettingsResponse | null): boolean {
  const own = (s?.overrides?.alerts ?? {}) as Partial<OiAlertSettings>;
  return Array.isArray(own.types) && own.types.includes(OPPOSITE);
}

/** The body that turns the bell on / off, keeping every other override and tick; on also asks the readings to follow
 *  the symbol (the readings the alert needs). Pure. */
export function bellBody(s: OiSettingsResponse, on: boolean, patch: Partial<OiAlertSettings> = {}) {
  const own = (s.overrides.alerts ?? {}) as Partial<OiAlertSettings>;
  const effective = (s.effective.alerts ?? {}) as Partial<OiAlertSettings>;
  const types = withType(own.types ?? effective.types ?? [], OPPOSITE, on);
  return { ...(on ? { enabled: true } : {}), overrides: { ...s.overrides, alerts: { ...own, ...patch, types } } };
}

/** A typed number for one setting; null when it is not a number inside its range. Pure. */
export function settingOf(text: string, min: number, max: number, integer = false): number | null {
  const v = Number(text.trim());
  if (!text.trim() || !Number.isFinite(v) || v < min || v > max || (integer && !Number.isInteger(v))) return null;
  return v;
}

export function OppositeOiBellView({ symbol, settings, note, onToggle, onSave }: {
  symbol: string; settings: OiSettingsResponse | null; note?: string | null;
  onToggle: (on: boolean) => void; onSave: (patch: Partial<OiAlertSettings>) => void;
}) {
  const on = bellOn(settings);
  const a = (settings?.effective.alerts ?? {}) as Partial<OiAlertSettings>;
  const Icon = on ? Bell : BellOff;
  return (
    <div className="space-y-space-2" data-opposite-oi={symbol}>
      <button type="button" aria-pressed={on} disabled={!settings} onClick={() => onToggle(!on)} data-opposite-oi-bell
        className={cx("inline-flex min-h-11 items-center gap-space-2 rounded-panel border px-space-3 text-t13 font-semibold md:min-h-9",
          on ? "border-brand bg-brand-tint text-fg" : "border-border text-fg-muted hover:text-fg", !settings && "opacity-50")}>
        <Icon aria-hidden className="h-4 w-4" />
        <span>{on ? "Opposite OI move alert: on" : "Alert me on opposite OI moves"}</span>
      </button>
      {on && (
        <div className="space-y-space-2 rounded-panel border border-subtle bg-surface-1 p-space-3 text-t12" data-opposite-oi-settings>
          <p className="text-fg-muted">One alert when the call OI and put OI totals (ATM ± 6) move in opposite directions in one window.
            Both rising or both falling: no alert.</p>
          <div className="flex flex-wrap items-center gap-space-2" role="radiogroup" aria-label="Window">
            <span className="font-medium text-fg-muted">Window</span>
            {([5, 10, 15] as const).map((n) => (
              <button key={n} type="button" role="radio" aria-checked={(a.opposite_window_minutes ?? REFERENCE.window) === n}
                onClick={() => onSave({ opposite_window_minutes: n })} data-opposite-oi-window={n}
                className={cx("min-h-9 rounded-panel border px-space-2", (a.opposite_window_minutes ?? REFERENCE.window) === n
                  ? "border-brand bg-brand-tint font-semibold text-fg" : "border-border text-fg-muted hover:text-fg")}>{n} min</button>
            ))}
          </div>
          <div className="grid grid-cols-1 gap-space-2 sm:grid-cols-3">
            <NumberField label="Each side changes at least (%)" value={a.opposite_min_change_pct ?? REFERENCE.pct} reference={REFERENCE.pct}
              parse={(t) => settingOf(t, 0, 100)} onSave={(v) => onSave({ opposite_min_change_pct: v })} id="pct" />
            <NumberField label="Sudden: at least k × the recent average" value={a.opposite_relative_k ?? REFERENCE.k} reference={REFERENCE.k}
              parse={(t) => settingOf(t, 0.01, 100)} onSave={(v) => onSave({ opposite_relative_k: v })} id="k" />
            <NumberField label="Recent average of the last M windows" value={a.opposite_relative_windows ?? REFERENCE.m} reference={REFERENCE.m}
              parse={(t) => settingOf(t, 1, 50, true)} onSave={(v) => onSave({ opposite_relative_windows: v })} id="m" />
          </div>
        </div>
      )}
      {note && <p className="text-t12 text-fg-muted" data-opposite-oi-note>{note}</p>}
    </div>
  );
}

function NumberField({ label, value, reference, parse, onSave, id }: {
  label: string; value: number; reference: number; parse: (t: string) => number | null; onSave: (v: number) => void; id: string;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const bad = draft !== null && parse(draft) === null;
  return (
    <label className="block">
      <span className="text-fg-muted">{label}</span>
      <input className={cx("mt-0.5 w-full rounded border bg-surface-2 px-2 py-1 font-tabular", bad ? "border-down" : "border-border")}
        inputMode="decimal" value={draft ?? formatNumber(value, 2, { trim: true })} data-opposite-oi-field={id} aria-invalid={bad || undefined}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => { if (draft === null) return; const v = parse(draft); if (v !== null) onSave(v); setDraft(null); }} />
      <span className="text-fg-muted">Reference {formatNumber(reference, 2, { trim: true })} - edit before use</span>
    </label>
  );
}

/** The bell with its settings for one symbol (loads and saves the symbol's own OI settings). */
export default function OppositeOiAlertV2({ symbol }: { symbol: string }) {
  const u = symbol.trim().toUpperCase();
  const [settings, setSettings] = useState<OiSettingsResponse | null>(null);
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    setSettings(null);
    // intentional: no OI settings for this symbol (the own-broker data mode, or no access) - the bell is simply not shown
    api.oiSettings(u).then((s) => { if (live) setSettings(s); }).catch(() => undefined);
    return () => { live = false; };
  }, [u]);
  if (!settings) return null;
  async function send(on: boolean, patch: Partial<OiAlertSettings> = {}) {
    if (!settings) return;
    try {
      const next = await api.saveOiSettings(u, bellBody(settings, on, patch));
      setSettings(next);
      setNote(on ? "Saved. The alert comes through your usual alert channels." : "Opposite OI move alert turned off.");
    } catch (e) { setNote(errorSentence(e)); }
  }
  return <OppositeOiBellView symbol={u} settings={settings} note={note} onToggle={(on) => send(on)} onSave={(p) => send(true, p)} />;
}

/** Settings → Alerts: the same bell for every symbol whose OI readings he follows; turned on only by him, per symbol. */
export function OppositeOiSymbolsV2() {
  const [names, setNames] = useState<string[] | null>(null);
  useEffect(() => {
    let live = true;
    // intentional: the followed symbols could not be read - the card is simply not shown (the bell stays on the chain)
    api.oiBanners().then((d) => { if (live) setNames((d.banners ?? []).map((b) => b.underlying)); }).catch(() => undefined);
    return () => { live = false; };
  }, []);
  if (!names) return null;
  return (
    <div id="opposite-oi" className="scroll-mt-20">
      <Card title="Opposite OI moves">
        {names.length === 0 ? <p className="text-t13 text-fg-muted" data-opposite-oi-empty>Turn it on from the option chain: the bell beside the OI history.</p> : (
          <div className="space-y-space-3" data-opposite-oi-symbols>
            {names.map((n) => <div key={n} className="space-y-space-1"><div className="text-t14 font-semibold text-fg">{n}</div><OppositeOiAlertV2 symbol={n} /></div>)}
          </div>
        )}
      </Card>
    </div>
  );
}
