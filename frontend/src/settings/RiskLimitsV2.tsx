import { useCallback, useEffect, useId, useState } from "react";
import { ApiError } from "../api/errors";
import { CardV2, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD, LABEL, NoteV2 } from "../trading/PartsV2";
import { Combobox } from "../ui/Combobox";
import { Details } from "../ui/Details";
import type { GlossaryKey } from "../ui/glossary";
import { TermHint } from "../ui/TermHint";
import { SWITCHES_OFF, SWITCH_FIELDS, loadSwitches, saveSwitches, type MyLimitSwitches } from "./myLimits";
import {
  bodyOf, draftOf, loadRiskLimits, onCount, saveRiskLimits, valueWords, type OppositePick, type PausePick, type RiskLimitCheck,
  type RiskLimitsDraft, type RiskLimitsView,
} from "./riskLimits";

/**
 * M64 (owner order 30 Oct + amendment): Settings › Risk limits at `/settings/risk-limits` (ui_v2). Every check is a
 * switch (off until the trader turns it on) with its value and a one-line (?) help; the guardian's rules and the two
 * order-ticket switches sit under "More checks". With everything off the order is the lots typed and only the broker /
 * exchange checks and the kill switch apply. Every block message links to its row here (`#<check>`).
 */
export default function RiskLimitsV2({ load = loadRiskLimits, save = saveRiskLimits }: {
  load?: typeof loadRiskLimits; save?: typeof saveRiskLimits;
} = {}) {
  const [view, setView] = useState<RiskLimitsView | null>(null);
  const [draft, setDraft] = useState<RiskLimitsDraft | null>(null);
  const [switches, setSwitches] = useState<MyLimitSwitches>(SWITCHES_OFF);
  const [switchesSaved, setSwitchesSaved] = useState<MyLimitSwitches>(SWITCHES_OFF);
  const [switchesVersion, setSwitchesVersion] = useState<number | null>(null);
  const [failed, setFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ kind: "done" | "warn"; text: string } | null>(null);

  const reload = useCallback(async () => {
    try {
      const v = await load();
      setView(v); setDraft(draftOf(v)); setFailed(false);
    } catch {
      setFailed(true);
    }
    try {
      const s = await loadSwitches();
      setSwitches(s.value); setSwitchesSaved(s.value); setSwitchesVersion(s.version);
    } catch { /* intentional: the two ticket switches stay as shown; the checks above still load */ }
  }, [load]);
  useEffect(() => { void reload(); }, [reload]);

  useEffect(() => {      // a block message's "Change limit" opens its row
    if (!view || typeof window === "undefined" || !window.location.hash) return;
    const el = document.getElementById(decodeURIComponent(window.location.hash.slice(1)));
    if (el) { el.closest("details")?.setAttribute("open", ""); el.scrollIntoView?.({ block: "center" }); }
  }, [view]);

  if (failed && !view) return <CardV2 title="Risk limits" testId="risk-limits"><LoadFailedV2 what="your risk limits" /></CardV2>;
  if (!view || !draft) return <CardV2 title="Risk limits" testId="risk-limits"><LoadingRowsV2 rows={5} /></CardV2>;

  const main = view.checks.filter((c) => c.group === "main");
  const more = view.checks.filter((c) => c.group === "more");
  const setCheck = (id: string, patch: Partial<RiskLimitsDraft["checks"][string]>) => {
    setDraft({ ...draft, checks: { ...draft.checks, [id]: { ...draft.checks[id], ...patch } } }); setNote(null);
  };
  const switchesChanged = SWITCH_FIELDS.some((f) => switches[f.key] !== switchesSaved[f.key]);

  async function onSave() {
    if (!view || !draft) return;
    const { body, problem } = bodyOf(view, draft);
    if (problem || !body) { setNote({ kind: "warn", text: problem ?? "Check the values." }); return; }
    setBusy(true); setNote(null);
    try {
      const next = await save(body);
      if (switchesChanged) {
        const s = await saveSwitches(switches, switchesVersion);
        setSwitches(s.value); setSwitchesSaved(s.value); setSwitchesVersion(s.version);
      }
      setView(next); setDraft(draftOf(next));
      setNote({ kind: "done", text: next.note ? `Saved. ${next.note}` : next.on ? `Saved. ${onCount(next)}.` : "Saved. All checks are off." });
    } catch (e) {
      setNote({ kind: "warn", text: e instanceof ApiError || e instanceof Error ? e.message : "Could not save. Try again." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-space-4" data-testid="risk-limits">
      <CardV2 title="Risk limits" badge={<span className="text-t12 font-medium text-fg-muted" data-testid="risk-limits-count">{onCount(view)}</span>}>
        <Details id="risk-limits.intro" className="mt-0"><p>Turn on only the checks you want. Off: your order is the lots you type. Exits are never checked.</p></Details>
        <ul className="mt-space-3 divide-y divide-subtle" data-testid="risk-limits-main">
          {main.map((c) => <CheckRow key={c.id} check={c} draft={draft} paper={view.paper?.[c.id]} onChange={setCheck}
            pause={draft.pause} onPause={(pause) => { setDraft({ ...draft, pause }); setNote(null); }} />)}
        </ul>
      </CardV2>
      <CardV2 title="More checks">
        <ul className="divide-y divide-subtle" data-testid="risk-limits-more">
          {more.map((c) => <CheckRow key={c.id} check={c} draft={draft} onChange={setCheck} />)}
          {SWITCH_FIELDS.map((f) => (
            <li key={f.key} id={f.key} className="scroll-mt-20 py-space-3" data-check={f.key}>
              <div className="flex items-center justify-between gap-space-3">
                <span className="min-w-0 text-t14 font-medium text-fg">{f.label}</span>
                <Switch on={switches[f.key]} label={f.label} onChange={(on) => { setSwitches({ ...switches, [f.key]: on }); setNote(null); }} />
              </div>
              <Details id={`risk-limits.${f.key}`} label="What it does" className="mt-space-1"><p>{f.help}</p></Details>
            </li>
          ))}
          {/* M68 PR-5 (TS1-052, owner decision D3): Not set = allowed; only his own "Not allowed" holds a bot's entry back */}
          <li id="opposite_positions" className="scroll-mt-20 py-space-3" data-check="opposite_positions">
            <label htmlFor="risk-limits-opposite" className={LABEL}>Two bots on opposite sides of one symbol</label>
            <Combobox id="risk-limits-opposite" label="Two bots on opposite sides of one symbol" value={draft.opposite} className="w-full sm:max-w-xs"
              onChange={(v) => { setDraft({ ...draft, opposite: v as OppositePick | "" }); setNote(null); }} options={OPPOSITE_OPTIONS}
              dataAttrs={{ "data-field": "opposite" }} />
            <Details id="risk-limits.opposite_positions" label="What it does" className="mt-space-1">
              <p>Not allowed: a bot does not enter against an open position another of your bots holds on the same symbol. Not set: allowed. Exits always work.</p>
            </Details>
          </li>
        </ul>
        <div className="mt-space-3">
          <label htmlFor="risk-limits-capital" className={LABEL}>Account basis for the % limits (₹)</label>
          <input id="risk-limits-capital" inputMode="decimal" className={FIELD} placeholder="Your paper balance" value={draft.capital}
            onChange={(e) => { setDraft({ ...draft, capital: e.target.value }); setNote(null); }} data-testid="risk-limits-capital" />
        </div>
      </CardV2>
      <div className="flex flex-wrap items-center gap-space-2">
        <button type="button" className={BUTTON_PRIMARY} disabled={busy} onClick={() => void onSave()} data-testid="risk-limits-save">
          {busy ? "Saving…" : "Save risk limits"}
        </button>
        <button type="button" className={BUTTON_SECONDARY} disabled={busy}
          onClick={() => { setDraft(draftOf(view)); setSwitches(switchesSaved); setNote(null); }}>Undo changes</button>
      </div>
      {note && <NoteV2 kind={note.kind} testId="risk-limits-note">{note.text}</NoteV2>}
      <p className="text-t12 text-fg-muted">{view.exits}</p>
    </div>
  );
}

/** M63 I-7 (?) on jargon: the checks whose name is a trading term carry the glossary's help mark. */
const CHECK_TERMS: Record<string, { term: GlossaryKey }> = {
  daily_loss: { term: "daily-loss-limit" },
  loss_cooldown: { term: "cool-down" },
  stop_cooldown: { term: "cool-down" },
  min_reward_risk: { term: "reward-risk" },
  margin_safety: { term: "margin" },
  drawdown: { term: "drawdown" },
};

// M68 PR-5 (TS1-051): no pre-picked choice - "Not set" until the trader picks one (asked when the check needs it).
const PAUSE_OPTIONS = [{ value: "", label: "Not set" }, { value: "next_session", label: "At the next session" },
  { value: "until_resume", label: "When I resume" }] as const;
const OPPOSITE_OPTIONS = [{ value: "", label: "Not set (allowed)" }, { value: "allow", label: "Allowed" }, { value: "deny", label: "Not allowed" }] as const;

function Switch({ on, onChange, label }: { on: boolean; onChange: (on: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} onClick={() => onChange(!on)}
      className="inline-flex min-h-11 shrink-0 items-center gap-space-2 rounded-control px-space-1 text-t13 font-medium text-fg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9">
      <span className={`relative h-6 w-11 rounded-full transition-colors ${on ? "bg-brand" : "bg-surface-3"}`}>
        <span className={`absolute top-0.5 h-5 w-5 rounded-full bg-surface-1 shadow ring-1 ring-border transition-all ${on ? "left-[1.375rem]" : "left-0.5"}`} />
      </span>
      <span className={on ? "text-up" : undefined}>{on ? "On" : "Off"}</span>
    </button>
  );
}

function CheckRow({ check, draft, onChange, paper, pause, onPause }: {
  check: RiskLimitCheck; draft: RiskLimitsDraft; onChange: (id: string, patch: Partial<RiskLimitsDraft["checks"][string]>) => void;
  paper?: Record<string, number>; pause?: PausePick | ""; onPause?: (p: PausePick | "") => void;
}) {
  const id = useId();
  const d = draft.checks[check.id];
  const shown = valueWords(check);
  const minutes = d.values.loss_cooldown_minutes ?? "";
  return (
    <li id={check.id} className="scroll-mt-20 py-space-3" data-check={check.id} data-on={d.enabled ? "on" : "off"}>
      <div className="flex items-center justify-between gap-space-3">
        <div className="min-w-0">
          <p className="text-t14 font-medium text-fg">{check.label}{CHECK_TERMS[check.id] && <> <TermHint term={CHECK_TERMS[check.id].term} /></>}</p>
          {!d.enabled && check.saved && shown && <p className="text-t12 text-fg-muted">Saved: {shown}</p>}
        </div>
        <Switch on={d.enabled} label={check.label} onChange={(on) => onChange(check.id, { enabled: on })} />
      </div>
      {/* the value fields show while the check is on (off: the saved value, if any, in the line above) */}
      {d.enabled && <div className="mt-space-2 grid grid-cols-1 gap-space-2 sm:grid-cols-2">
        {check.fields.map((f) => (
          <div key={f.key} className="min-w-0">
            <label htmlFor={`${id}-${f.key}`} className={LABEL}>{f.label}{f.unit.toLowerCase() === f.label.toLowerCase() ? "" : ` (${f.unit})`}</label>
            <input id={`${id}-${f.key}`} inputMode="decimal" className={FIELD} value={d.values[f.key] ?? ""}
              placeholder={f.optional ? "Optional" : "Value"} aria-describedby={`${id}-help`}
              onChange={(e) => onChange(check.id, { values: { ...d.values, [f.key]: e.target.value } })} data-field={f.key} />
          </div>
        ))}
        {check.id === "loss_cooldown" && onPause && minutes.trim() === "" && (
          <div className="min-w-0">
            <label htmlFor={`${id}-pause`} className={LABEL}>With no minutes, the pause ends</label>
            <Combobox id={`${id}-pause`} label="With no minutes, the pause ends" value={pause ?? ""} className="w-full"
              onChange={(v) => onPause(v as PausePick | "")} options={PAUSE_OPTIONS} dataAttrs={{ "data-field": "pause" }} />
          </div>
        )}
      </div>}
      {paper && <p className="mt-space-1 text-t12 text-fg-muted">Paper ticket holds {Object.values(paper).join(" · ")} from before; saving here sets both.</p>}
      <Details id={`risk-limits.${check.id}`} label="What it does" className="mt-space-1"><p id={`${id}-help`}>{check.help}</p></Details>
    </li>
  );
}
