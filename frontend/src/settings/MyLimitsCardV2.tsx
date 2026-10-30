import { useCallback, useEffect, useId, useState } from "react";
import { Link } from "react-router";
import { api } from "../api/client";
import { CardV2, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD, LABEL, NoteV2 } from "../trading/PartsV2";
import type { RiskConfig } from "../types";
import { Details } from "../ui/Details";
import {
  LIMIT_FIELDS, NOT_SET, SWITCHES_OFF, SWITCH_FIELDS, configOf, countSet, loadSwitches, saveSwitches, textOf,
  type LimitsText, type MyLimitSwitches,
} from "./myLimits";
import { RISK_LIMITS_PATH } from "./riskLimits";

/**
 * DEF-0 D9 (owner, 28 Oct): one "My limits" card (Risk page and Settings -> Trading defaults). Every limit is the
 * trader's own; a blank one reads "Off" and nothing holds an order back for it. No forced setup: with
 * nothing set the card offers "Set my limits" and nothing else.
 */
export default function MyLimitsCardV2({ testId = "my-limits", editHere = false }: { testId?: string; editHere?: boolean }) {
  // M64 RM-5: the limits are set on Settings › Risk limits (one source); this card now points there. `editHere` keeps the
  // old card (code kept, CLAUDE.md section 8).
  if (!editHere) {
    return (
      <CardV2 title="My limits" testId={testId}>
        <p className="text-t13 text-fg-muted">Every risk check is a switch, off until you turn it on. Exits always work.</p>
        <Link to={RISK_LIMITS_PATH} className="mt-space-2 inline-flex min-h-11 items-center text-t14 font-medium text-brand underline md:min-h-9"
          data-testid="my-limits-open">Open Settings › Risk limits</Link>
      </CardV2>
    );
  }
  return <MyLimitsEditor testId={testId} />;
}

function MyLimitsEditor({ testId }: { testId: string }) {
  const id = useId();
  const [saved, setSaved] = useState<RiskConfig | null>(null);
  const [text, setText] = useState<LimitsText | null>(null);
  const [switches, setSwitches] = useState<MyLimitSwitches>(SWITCHES_OFF);
  const [switchesVersion, setSwitchesVersion] = useState(0);
  const [editing, setEditing] = useState(false);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ kind: "done" | "warn"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const [config, s] = await Promise.all([api.getRiskSettings(), loadSwitches()]);
      setSaved(config); setText(textOf(config)); setSwitches(s.value); setSwitchesVersion(s.version); setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);
  useEffect(() => { void load(); }, [load]);

  async function save() {
    if (!text) return;
    const { config, problem } = configOf(text, saved);
    if (problem || !config) { setNote({ kind: "warn", text: problem ?? "Check the values." }); return; }
    setBusy(true); setNote(null);
    try {
      const next = await api.updateRiskSettings(config);
      const s = await saveSwitches(switches, switchesVersion);
      setSaved(next); setText(textOf(next)); setSwitches(s.value); setSwitchesVersion(s.version); setEditing(false);
      const n = countSet(next);
      setNote({ kind: "done", text: n ? `Saved. ${n} of your limits ${n === 1 ? "is" : "are"} on; the rest are not set.` : "Saved. All limits are off." });
    } catch (e) {
      setNote({ kind: "warn", text: e instanceof Error ? e.message : "Could not save. Try again." });
    } finally {
      setBusy(false);
    }
  }

  if (failed) return <CardV2 title="My limits" testId={testId}><LoadFailedV2 what="your limits" /></CardV2>;
  if (!text) return <CardV2 title="My limits" testId={testId}><LoadingRowsV2 /></CardV2>;
  const anySet = countSet(saved) > 0 || switches.limit_by_experience || switches.stop_required_on_sold;

  return (
    <CardV2 title="My limits" testId={testId}>
      <p className="text-t13 text-fg-muted">Your own limits. A blank one is not set: nothing is held back for it. Exits always work.</p>
      {!editing && !anySet && (
        <div className="mt-space-3 flex flex-wrap items-center gap-space-3" data-testid="my-limits-empty">
          <span className="text-t14 text-fg">All limits are off.</span>
          <button type="button" className={BUTTON_SECONDARY} onClick={() => setEditing(true)} data-testid="my-limits-set">Set my limits</button>
        </div>
      )}
      {(editing || anySet) && (
        <div className="mt-space-3 grid gap-space-3 sm:grid-cols-2" data-testid="my-limits-fields">
          {LIMIT_FIELDS.map((f) => (
            <div key={f.key} data-limit={f.key}>
              <label htmlFor={`${id}-${f.key}`} className={LABEL}>{f.label} <span className="font-normal">({f.unit})</span></label>
              {editing ? (
                <input id={`${id}-${f.key}`} inputMode="decimal" className={FIELD} placeholder={NOT_SET} value={text[f.key]}
                  onChange={(e) => setText({ ...text, [f.key]: e.target.value })} />
              ) : (
                <p id={`${id}-${f.key}`} className="text-t14 text-fg">{text[f.key] === "" ? NOT_SET : `${text[f.key]} ${f.unit}`}</p>
              )}
              <Details id={`my-limits.${f.key}`} className="mt-space-1"><p>{f.help}</p></Details>
            </div>
          ))}
          {SWITCH_FIELDS.map((f) => (
            <div key={f.key} className="sm:col-span-2" data-limit={f.key}>
              <label className="flex min-h-11 items-center gap-space-2 text-t14 text-fg">
                <input type="checkbox" checked={switches[f.key]} disabled={!editing}
                  onChange={(e) => setSwitches({ ...switches, [f.key]: e.target.checked })} />
                {f.label} <span className="text-fg-muted">({switches[f.key] ? "on" : "off"})</span>
              </label>
              <Details id={`my-limits.${f.key}`}><p>{f.help}</p></Details>
            </div>
          ))}
        </div>
      )}
      {(editing || anySet) && (
        <div className="mt-space-3 flex flex-wrap gap-space-2">
          {editing ? (
            <>
              <button type="button" className={BUTTON_PRIMARY} disabled={busy} onClick={() => void save()} data-testid="my-limits-save">
                {busy ? "Saving…" : "Save my limits"}
              </button>
              <button type="button" className={BUTTON_SECONDARY} disabled={busy}
                onClick={() => { setEditing(false); setText(textOf(saved)); setNote(null); }}>Cancel</button>
            </>
          ) : (
            <button type="button" className={BUTTON_SECONDARY} onClick={() => setEditing(true)} data-testid="my-limits-edit">Change my limits</button>
          )}
        </div>
      )}
      {note && <div className="mt-space-2"><NoteV2 kind={note.kind}>{note.text}</NoteV2></div>}
    </CardV2>
  );
}
