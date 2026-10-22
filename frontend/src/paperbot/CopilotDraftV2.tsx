import { Sparkles, TriangleAlert } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/errors";
import { CardV2 } from "../dashboard/CardV2";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD, LABEL, NoteV2 } from "../trading/PartsV2";
import type { CustomStrategyConfig, CustomStrategyResponse } from "../types";
import {
  answerProblem, askKind, draftConfig, DRAFT_TIMEFRAMES, openAsks, saveLine, type DraftValues, type StrategyDraft,
} from "../copilot/strategyDraft";

/**
 * Item 5 (owner's handover), job 6: the Copilot's draft on the Paper bot page (v2 only, with the `ai_copilot` switch on).
 *
 * Top to bottom: what this is ("Built from your words... nothing is saved yet"), the strategy's name, its rules in plain
 * words per side, "Values you decide" - one field per value the trader's words did not state, EMPTY until he fills it in
 * (the Copilot never suggests a number) - his stated values, "Not supported yet" / "Parts I could not read", then
 * "Save as my strategy" (off until every value is set) and "Discard the draft". Saving is the trader's own click on the
 * existing custom-strategy route; the page then picks the saved strategy in "Start a paper bot", where he presses
 * "Start on paper" himself. Nothing here starts a bot or touches an order (ADR-0006); PAPER only.
 */
export default function CopilotDraftV2({ draft, onSaved, onDiscard, save = api.createCustomStrategy }: {
  draft: StrategyDraft;
  onSaved: (saved: CustomStrategyResponse) => void;
  onDiscard: () => void;
  /** The save call (the existing custom-strategy route); a test passes its own. */
  save?: (config: CustomStrategyConfig) => Promise<CustomStrategyResponse>;
}) {
  const ids = { name: useId(), minRr: useId(), leftOut: useId() };
  const [name, setName] = useState(draft.name);
  const [values, setValues] = useState<DraftValues>({});
  const [touched, setTouched] = useState<Record<string, boolean>>({});
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const config = useMemo(() => draftConfig(draft, values, name), [draft, values, name]);
  const set = (key: string, value: string) => { setValues((v) => ({ ...v, [key]: value })); setProblem(null); };
  const sides: ("long" | "short" | null)[] = ["long", "short", null];
  const minRrProblem = "min_rr" in values ? answerProblem("min_rr", values.min_rr) : null;

  async function onSave() {
    setTouched(Object.fromEntries(draft.asks.map((a) => [a.key, true])));
    if (!config || minRrProblem) return;
    setBusy(true); setProblem(null);
    try {
      onSaved(await save(config));
    } catch (e) {
      setProblem(e instanceof ApiError && (e.status === 402 || e.status === 403)
        ? "Your plan or your role cannot save another strategy. Remove one you no longer use, or ask your organisation's owner."
        : e instanceof ApiError && e.status === 400
          ? "ATP could not use one of the values. Check them and try again."
          : "The strategy could not be saved just now. Try again in a moment.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <CardV2 title="Your draft from the Copilot" testId="copilot-draft">
      <div className="space-y-space-4">
        <p className="flex items-start gap-space-2 text-t14 text-fg-muted">
          <Sparkles size={16} aria-hidden className="mt-0.5 shrink-0 text-brand" />
          <span>Built from your own words with ATP&apos;s building blocks. Nothing is saved yet: fill in your values, save it as your
            strategy, then start it on paper below. Every number is yours.</span>
        </p>

        <div>
          <label htmlFor={ids.name} className={LABEL}>Name</label>
          <input id={ids.name} className={FIELD} value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
        </div>

        {sides.map((side) => {
          const rules = draft.rules.filter((r) => r.side === side);
          if (rules.length === 0) return null;
          return (
            <section key={side ?? "open"} className="rounded-control border border-subtle bg-surface-2 p-space-3" data-draft-side={side ?? "open"}>
              <h3 className="text-t13 font-semibold text-fg">
                {side === "long" ? "Long side" : side === "short" ? "Short side" : "Side to decide"}: all of these hold
                {draft.timeframe ? ` on the ${DRAFT_TIMEFRAMES.find((t) => t.value === draft.timeframe)?.chart ?? draft.timeframe} chart` : ""}
              </h3>
              <ul className="mt-space-1 list-disc space-y-space-1 pl-space-5">
                {rules.map((r) => <li key={r.text + r.words} className="text-t14 text-fg">{r.text}</li>)}
              </ul>
            </section>
          );
        })}

        {draft.asks.length > 0 && (
          <fieldset className="space-y-space-3" data-asks>
            <legend className="text-t14 font-semibold text-fg">Values you decide</legend>
            <div className="grid gap-space-3 sm:grid-cols-2">
              {draft.asks.map((a) => (
                <AskField key={a.key} askKey={a.key} label={a.label} value={values[a.key] ?? ""} touched={!!touched[a.key]}
                  onChange={(v) => set(a.key, v)} onBlur={() => setTouched((t) => ({ ...t, [a.key]: true }))} />
              ))}
            </div>
          </fieldset>
        )}

        <StatedValues draft={draft} minRrId={ids.minRr} minRr={values.min_rr} minRrProblem={minRrProblem} onMinRr={(v) => set("min_rr", v)} />

        {(draft.unsupported.length > 0 || draft.unread.length > 0) && (
          <section data-note="draft-left-out" aria-labelledby={ids.leftOut}
            className="flex items-start gap-space-2 rounded-panel border border-warn/40 bg-warn-tint px-space-4 py-space-3 text-t14 text-fg">
            <TriangleAlert size={16} aria-hidden className="mt-0.5 shrink-0 text-warn" />
            <div className="min-w-0">
              <h3 id={ids.leftOut} className="font-semibold">Left out of this draft</h3>
              <ul className="mt-space-1 list-disc space-y-space-1 pl-space-5">
                {draft.unsupported.map((u) => <li key={u}>Not supported yet: {u}.</li>)}
                {draft.unread.map((u) => <li key={u}>Not read: &ldquo;{u}&rdquo;.</li>)}
              </ul>
              <p className="mt-space-1">The draft is not your whole idea: decide whether it still fits before you save it.</p>
            </div>
          </section>
        )}

        <div className="space-y-space-2">
          <p className="text-t13 text-fg-muted" data-save-line>{saveLine(draft, values)}</p>
          <div className="flex flex-col gap-space-2 sm:flex-row">
            <button type="button" className={`${BUTTON_PRIMARY} w-full sm:w-auto`} disabled={busy || !config || !!minRrProblem}
              aria-busy={busy || undefined} onClick={() => void onSave()}>
              Save as my strategy
            </button>
            <button type="button" className={`${BUTTON_SECONDARY} w-full sm:w-auto`} onClick={onDiscard}>Discard the draft</button>
          </div>
          {problem && <NoteV2 kind="warn">{problem}</NoteV2>}
        </div>
      </div>
    </CardV2>
  );
}

function AskField({ askKey, label, value, touched, onChange, onBlur }: {
  askKey: string; label: string; value: string; touched: boolean; onChange: (v: string) => void; onBlur: () => void;
}) {
  const id = useId();
  const hint = useId();
  const kind = askKind(askKey);
  const problem = touched ? answerProblem(askKey, value) : null;
  const common = { id, className: FIELD, value, onBlur, "aria-invalid": !!problem, "aria-describedby": problem ? hint : undefined, "data-ask": askKey };
  return (
    <div>
      <label htmlFor={id} className={LABEL}>{label}</label>
      {kind === "side" ? (
        <select {...common} onChange={(e) => onChange(e.target.value)}>
          <option value="">Choose…</option><option value="long">Long</option><option value="short">Short</option><option value="both">Both</option>
        </select>
      ) : kind === "timeframe" ? (
        <select {...common} onChange={(e) => onChange(e.target.value)}>
          <option value="">Choose…</option>
          {DRAFT_TIMEFRAMES.map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
      ) : kind === "direction" ? (
        <select {...common} onChange={(e) => onChange(e.target.value)}>
          <option value="">Choose…</option><option value="CROSSES_ABOVE">Crosses above</option><option value="CROSSES_BELOW">Crosses below</option>
        </select>
      ) : (
        <input {...common} inputMode="decimal" autoComplete="off" placeholder={kind === "pair" ? "Your number (or two)" : "Your number"}
          onChange={(e) => onChange(e.target.value)} />
      )}
      {problem && <p id={hint} role="alert" className="mt-space-1 text-t13 text-warn">{problem}</p>}
    </div>
  );
}

function StatedValues({ draft, minRrId, minRr, minRrProblem, onMinRr }: {
  draft: StrategyDraft; minRrId: string; minRr: string | undefined; minRrProblem: string | null; onMinRr: (v: string) => void;
}) {
  const s = draft.settings;
  const lines: string[] = [];
  if (draft.timeframe) lines.push(`Timeframe: ${DRAFT_TIMEFRAMES.find((t) => t.value === draft.timeframe)?.label ?? draft.timeframe}`);
  if (s.stop_loss_atr_mult !== null) lines.push(`Where the idea is wrong: ${s.stop_loss_atr_mult} x ATR from the entry`);
  if (s.atr_period !== null) lines.push(`ATR length: ${s.atr_period}`);
  if (s.target_rr) lines.push(s.target_rr[0] === s.target_rr[1] ? `Profit objective: ${s.target_rr[0]}R` : `Profit objectives: ${s.target_rr[0]}R, then ${s.target_rr[1]}R`);
  if (draft.lots !== null) lines.push(`Size: ${draft.lots} ${draft.lots === 1 ? "lot" : "lots"} (set on the bot below)`);
  const showMinRr = s.min_rr !== null && !draft.asks.some((a) => a.key === "min_rr");
  if (lines.length === 0 && !showMinRr) return null;
  return (
    <section className="space-y-space-2" data-stated>
      <h3 className="text-t14 font-semibold text-fg">From your words</h3>
      {lines.length > 0 && <ul className="list-disc space-y-space-1 pl-space-5">{lines.map((l) => <li key={l} className="text-t14 text-fg">{l}</li>)}</ul>}
      {showMinRr && (
        <div className="max-w-xs">
          <label htmlFor={minRrId} className={LABEL}>
            Minimum reward to risk ({draft.min_rr_source === "your risk limits" ? "from your own risk limits" : "from your words"})
          </label>
          <input id={minRrId} className={FIELD} inputMode="decimal" autoComplete="off" value={minRr ?? String(s.min_rr)}
            aria-invalid={!!minRrProblem} onChange={(e) => onMinRr(e.target.value)} />
          {minRrProblem && <p role="alert" className="mt-space-1 text-t13 text-warn">{minRrProblem}</p>}
        </div>
      )}
    </section>
  );
}
