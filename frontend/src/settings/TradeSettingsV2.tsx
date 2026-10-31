import { useCallback, useEffect, useId, useState } from "react";
import { Link } from "react-router";
import { CardV2, LoadFailedV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { BUTTON_PRIMARY, FIELD, LABEL, NoteV2 } from "../trading/PartsV2";
import { Combobox } from "../ui/Combobox";
import { Details } from "../ui/Details";
import { TermHint } from "../ui/TermHint";
import {
  NOT_SET, changesOf, draftOf, editable, loadTradeSettings, partsOf, saveTradeSettings, setCount, termOf, valueWords,
  type Draft, type TradeField, type TradeSection, type TradeSettingsView,
} from "./tradeSettings";

/**
 * M68 PR-0 (owner order M68, TS-2): Settings › Trade settings at `/settings/trade-settings` (ui_v2) - the seven sections,
 * every field "Not set" until the trader saves his own, a Save per section that sends only what he changed. A field
 * nothing reads yet is never shown to a trader (no dead control); a platform admin sees it under Advanced. Facts - what
 * the exchange or the broker sets - are listed as information.
 */
export default function TradeSettingsV2({ load = loadTradeSettings, save = saveTradeSettings, initial = null }: {
  load?: typeof loadTradeSettings; save?: typeof saveTradeSettings; initial?: TradeSettingsView | null;
} = {}) {
  const [view, setView] = useState<TradeSettingsView | null>(initial);
  const [failed, setFailed] = useState(false);

  const reload = useCallback(async () => {
    try { setView(await load()); setFailed(false); } catch { setFailed(true); }
  }, [load]);
  useEffect(() => { if (!initial) void reload(); }, [reload, initial]);

  if (failed && !view) return <CardV2 title="Trade settings" testId="trade-settings"><LoadFailedV2 what="your trade settings" /></CardV2>;
  if (!view) return <CardV2 title="Trade settings" testId="trade-settings"><LoadingRowsV2 rows={5} /></CardV2>;

  return (
    <div className="space-y-space-4" data-testid="trade-settings">
      <NoteV2 kind="info" testId="trade-settings-intro">ATP sets no trading value for you. Every choice starts as {NOT_SET} until you save your own.</NoteV2>
      {view.sections.map((s) => <SectionCard key={s.id} section={s} admin={view.admin} save={save} onSaved={setView} />)}
    </div>
  );
}

function SectionCard({ section, admin, save, onSaved }: {
  section: TradeSection; admin: boolean; save: typeof saveTradeSettings; onSaved: (v: TradeSettingsView) => void;
}) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(section));
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ kind: "done" | "warn"; text: string } | null>(null);
  // A save returns the whole view: refill this form only when this section's own saved values changed.
  const savedSig = JSON.stringify(section.fields.map((f) => [f.key, f.value ?? null]));
  useEffect(() => { setDraft(draftOf(section)); }, [savedSig]); // eslint-disable-line react-hooks/exhaustive-deps

  const { main, facts, advanced } = partsOf(section, admin);
  const keys = [...main, ...advanced].filter(editable).map((f) => f.key);
  const count = setCount(section, admin);
  const set = (key: string, text: string) => { setDraft((d) => ({ ...d, [key]: text })); setNote(null); };

  const onSave = async () => {
    const { values, problem } = changesOf(section, draft, keys);
    if (problem) { setNote({ kind: "warn", text: problem }); return; }
    if (!Object.keys(values).length) { setNote({ kind: "done", text: "Nothing changed." }); return; }
    setBusy(true);
    try {
      onSaved(await save(values));
      setNote({ kind: "done", text: "Saved." });
    } catch {
      setNote({ kind: "warn", text: "Not saved. Check your connection and try again." });
    } finally {
      setBusy(false);
    }
  };

  return (
    <section id={section.id} className="scroll-mt-20" data-section={section.id}>
      <CardV2 title={section.title} badge={count ? <span className="text-t12 font-medium text-fg-muted" data-section-count>{count}</span> : undefined}>
        {main.length > 0 ? (
          <ul className="divide-y divide-subtle" data-part="main">
            {main.map((f) => <FieldRow key={f.key} field={f} text={draft[f.key] ?? ""} onText={set} />)}
          </ul>
        ) : (
          <p className="text-t13 text-fg-muted" data-part="empty">
            {section.today
              ? <>For now you set these in <Link to={section.today.path} className="font-medium text-brand hover:underline">{section.today.label}</Link>.</>
              : "Nothing to choose here yet."}
          </p>
        )}
        {facts.length > 0 && (
          <Details id={`trade-settings.${section.id}.facts`} label="Set by the exchange or your broker" className="mt-space-2">
            <ul className="space-y-space-2" data-part="facts">
              {facts.map((f) => (
                <li key={f.key} data-fact={f.key}>
                  <p className="flex items-center gap-space-1 text-t13 font-medium text-fg">{f.label}{termOf(f) && <TermHint term={termOf(f)!} />}</p>
                  <p className="text-t12 text-fg-muted">{f.help}</p>
                </li>
              ))}
            </ul>
          </Details>
        )}
        {advanced.length > 0 && (
          <Details id={`trade-settings.${section.id}.advanced`} label={`Advanced (admin): not wired yet · ${advanced.length}`} className="mt-space-2">
            <ul className="divide-y divide-subtle" data-part="advanced">
              {advanced.map((f) => <FieldRow key={f.key} field={f} text={draft[f.key] ?? ""} onText={set} />)}
            </ul>
          </Details>
        )}
        {keys.length > 0 && (
          <div className="mt-space-3 flex flex-wrap items-center gap-space-3">
            <button type="button" className={BUTTON_PRIMARY} disabled={busy} onClick={() => void onSave()} data-save={section.id}>
              Save {section.title.toLowerCase()}
            </button>
          </div>
        )}
        {note && <div className="mt-space-2"><NoteV2 kind={note.kind} testId={`trade-settings-note-${section.id}`}>{note.text}</NoteV2></div>}
      </CardV2>
    </section>
  );
}

const SWITCH_OPTIONS = [{ value: "", label: NOT_SET }, { value: "on", label: "On" }, { value: "off", label: "Off" }];

function FieldRow({ field: f, text, onText }: { field: TradeField; text: string; onText: (key: string, text: string) => void }) {
  const id = useId();
  const term = termOf(f);
  const unit = f.unit && f.kind !== "choice" && f.kind !== "switch" ? ` (${f.unit})` : "";
  return (
    <li className="grid grid-cols-1 gap-space-2 py-space-3 sm:grid-cols-[minmax(0,1fr)_minmax(0,14rem)] sm:items-start" data-field={f.key}
      data-saved={f.saved ? "yes" : "no"}>
      <div className="min-w-0">
        <label htmlFor={`${id}-input`} className={LABEL}>{f.label}{unit}{term && <TermHint term={term} />}</label>
        <p id={`${id}-help`} className="text-t12 text-fg-muted">{f.help}</p>
        <p className={`text-t12 ${f.saved ? "text-fg" : "text-fg-muted"}`} data-value>{f.saved ? `Saved: ${valueWords(f)}` : NOT_SET}</p>
      </div>
      <div className="min-w-0">
        {!editable(f) ? (
          <p className="text-t13 text-fg-muted" data-readonly>{f.scope === "organisation" ? "Set by your admin for the team" : valueWords(f)}</p>
        ) : f.kind === "choice" || f.kind === "switch" ? (
          <Combobox id={`${id}-input`} label={f.label} value={text} className="w-full" onChange={(v) => onText(f.key, v)}
            options={f.kind === "switch" ? SWITCH_OPTIONS : [{ value: "", label: NOT_SET }, ...f.choices]} dataAttrs={{ "data-input": f.key }} />
        ) : (
          <input id={`${id}-input`} className={FIELD} value={text} aria-describedby={`${id}-help`} placeholder={NOT_SET}
            inputMode={f.kind === "time" ? "numeric" : "decimal"} onChange={(e) => onText(f.key, e.target.value)} data-input={f.key} />
        )}
      </div>
    </li>
  );
}
