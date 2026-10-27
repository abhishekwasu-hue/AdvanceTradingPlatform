import { Clock, Pencil, ShieldCheck, TriangleAlert, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ComponentProps } from "react";
import { Dialog, Sheet } from "../components/primitives/Dialog";
import { useMediaQuery } from "../utils/useMediaQuery";
import { cx } from "../components/primitives/cx";
import { dataOf, usePolled } from "../dashboard/useDashboardData";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD, LABEL } from "../trading/PartsV2";
import type { TradeRecord } from "../types";
import { formatINR, formatNumber } from "../ui/format";
import {
  MEASURES, TIMEFRAMES, active, amount, bodyOf, chipText, combinedApi, defaultMeasure, exitShortCopy, failText, formErrors,
  newRef, previewWords, sheetLegs, staleText, trailLabels, trailOf, type CombinedApi, type CombinedExit, type CombinedForm,
  type CombinedPreview, type SheetLeg, type TrailKind,
} from "./combinedExit";
import type { ContractFacts } from "./contracts";
import { groupExitApi, runExit, type GroupExitApi } from "./groupExits";

/**
 * CX6 (owner, 24 Oct): "Set combined stop-loss" on the positions panel (Terminal and My trades), the sheet (tick boxes per
 * leg, a ₹ / spot / premium level, the plain preview sentence, confirm) and the active combined exit as a chip with the
 * distance to trigger, the CX-F1 "stale price" mark and Edit / Remove. CX-F2: the one-tap "Exit short now" with confirm.
 *
 * Shown only with `ui_v2` and the PAPER account on (the routes answer 404 otherwise). Setting one sends no order: the
 * trigger engine closes the legs as one group when the level is reached, the sold legs first, never blocked (ADR-0004).
 * The page never suggests a level; the user types his own. Nothing here touches an exit button.
 */

const POLL_MS = 15_000;
const CHIP = "inline-flex min-w-0 max-w-full flex-wrap items-center gap-x-space-2 gap-y-space-1 rounded-control border px-space-3 py-space-2 text-t13";
const ICON_BUTTON = "inline-flex min-h-11 items-center gap-space-1 rounded-control px-space-2 text-t13 font-semibold text-brand hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand lg:min-h-9";

/** The caller's own combined exits, read every few seconds while `on` (a failure reads as none). */
export function useCombinedExits(on: boolean, api: Pick<CombinedApi, "list"> = combinedApi) {
  const [state, reload] = usePolled(api.list, () => POLL_MS, on);
  const data = on ? dataOf(state) : null;
  const list: CombinedExit[] = Array.isArray(data) ? data : [];
  return { list, reload };
}

// --- The chip ------------------------------------------------------------------------------------------------------------

export function CombinedExitChipV2({ exit, masked = false, onEdit, onRemove }: {
  exit: CombinedExit; masked?: boolean; onEdit?: (x: CombinedExit) => void; onRemove?: (x: CombinedExit) => void;
}) {
  const words = chipText(exit, masked);
  const stale = staleText(exit);
  const armed = exit.status === "ARMED";
  return (
    <li className={cx(CHIP, stale ? "border-warn bg-warn-tint" : "border-border bg-surface-2")} data-combined-exit={exit.id}
      data-status={exit.status} title={exit.words}>
      <ShieldCheck size={14} aria-hidden className="shrink-0 text-fg-muted" />
      <span className="font-semibold text-fg">{words.title}</span>
      <span className={cx("font-tabular", words.closing ? "text-warn" : "text-fg-muted")} data-distance>{words.distance}</span>
      {words.stop && <span className="font-tabular text-fg" data-stop-now>{words.stop}</span>}
      {stale && (
        <span className="inline-flex items-center gap-space-1 font-semibold text-warn" data-stale title={stale}>
          <Clock size={14} aria-hidden />Stale price
        </span>
      )}
      {armed && onEdit && (
        <button type="button" className={ICON_BUTTON} onClick={() => onEdit(exit)} data-testid="combined-edit">
          <Pencil size={14} aria-hidden />Edit
        </button>
      )}
      {armed && onRemove && (
        <button type="button" className={ICON_BUTTON} onClick={() => onRemove(exit)} data-testid="combined-remove">
          <X size={14} aria-hidden />Remove
        </button>
      )}
      {stale && <span className="sr-only">{stale}</span>}
    </li>
  );
}

// --- The sheet's body (pure: rendered and tested on its own) ---------------------------------------------------------

export interface FormView {
  legs: readonly SheetLeg[];
  form: CombinedForm;
  /** Editing an exit already set: the legs and what it watches stay; only the levels change. */
  editing: boolean;
  preview: CombinedPreview | null;
  checking: boolean;
}

export function CombinedExitFormV2({ view, onChange }: { view: FormView; onChange: (next: CombinedForm) => void }) {
  const { legs, form, editing, preview, checking } = view;
  const set = (patch: Partial<CombinedForm>) => onChange({ ...form, ...patch });
  const allTicked = legs.length > 0 && legs.every((l) => form.ticked.includes(l.id));
  const errors = formErrors(form, legs);
  const local = previewWords(form, legs);
  const words = preview?.ok && preview.words ? preview.words : local;
  const reasons = preview && !preview.ok ? preview.reasons : [];
  const rupees = form.measure === "MTM_INR";
  const slLabel = rupees ? "Exit if the combined loss reaches (₹)" : form.measure === "SPOT" ? "Stop-loss: spot level" : "Stop-loss: net premium";
  const targetLabel = rupees ? "Exit if the combined profit reaches (₹, optional)" : form.measure === "SPOT" ? "Target: spot level (optional)" : "Target: net premium (optional)";
  const now = preview?.value_now ?? null;
  return (
    <div className="space-y-space-4 text-t14 text-fg" data-combined-form>
      <fieldset className="space-y-space-1" disabled={editing}>
        <legend className={LABEL}>Positions in this exit</legend>
        {!editing && legs.length > 1 && (
          <label className="flex min-h-11 items-center gap-space-2 lg:min-h-9">
            <input type="checkbox" checked={allTicked} data-testid="combined-all"
              onChange={(e) => set({ ticked: e.target.checked ? legs.map((l) => l.id) : [] })} className="h-4 w-4 accent-brand" />
            <span className="font-semibold">All open positions ({legs.length})</span>
          </label>
        )}
        <ul className="space-y-px">
          {legs.map((l) => (
            <li key={l.id}>
              <label className="flex min-h-11 items-center gap-space-2 lg:min-h-9" data-leg={l.id}>
                <input type="checkbox" checked={form.ticked.includes(l.id)} className="h-4 w-4 accent-brand"
                  onChange={(e) => set({ ticked: e.target.checked ? [...form.ticked, l.id] : form.ticked.filter((id) => id !== l.id) })} />
                <span className="min-w-0 flex-1 truncate">{l.symbol}</span>
                <span className="shrink-0 text-t12 text-fg-muted">{l.short ? "Sold" : "Bought"}</span>
              </label>
            </li>
          ))}
        </ul>
        {editing && <p className="text-t12 text-fg-muted">To change the positions, remove this exit and set a new one.</p>}
      </fieldset>

      <fieldset className="space-y-space-1" disabled={editing}>
        <legend className={LABEL}>What the exit watches</legend>
        <div className="grid grid-cols-1 gap-space-1 sm:grid-cols-3">
          {MEASURES.map((m) => (
            <label key={m.key} className={cx("flex min-h-11 cursor-pointer items-start gap-space-2 rounded-control border px-space-3 py-space-2",
              form.measure === m.key ? "border-brand bg-surface-2" : "border-border")} data-measure={m.key}>
              <input type="radio" name="combined-measure" checked={form.measure === m.key} onChange={() => set({ measure: m.key })} className="mt-px h-4 w-4 accent-brand" />
              <span className="min-w-0">
                <span className="block text-t13 font-semibold">{m.label}</span>
                <span className="block text-t12 text-fg-muted">{m.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      <div className="grid grid-cols-1 gap-space-3 sm:grid-cols-2">
        <label className="block">
          <span className={LABEL}>{slLabel}</span>
          <input className={FIELD} inputMode="decimal" value={form.sl} onChange={(e) => set({ sl: e.target.value })} data-testid="combined-sl" autoComplete="off" />
        </label>
        <label className="block">
          <span className={LABEL}>{targetLabel}</span>
          <input className={FIELD} inputMode="decimal" value={form.target} onChange={(e) => set({ target: e.target.value })} data-testid="combined-target" autoComplete="off" />
        </label>
      </div>

      <TrailingPartV2 form={form} set={set} />

      {form.measure === "SPOT" && (
        <div className="grid grid-cols-1 gap-space-3 sm:grid-cols-2">
          <label className="block">
            <span className={LABEL}>Check the level</span>
            <select className={FIELD} value={form.check} onChange={(e) => set({ check: e.target.value === "CLOSE" ? "CLOSE" : "TOUCH" })} data-testid="combined-check">
              <option value="TOUCH">On every price</option>
              <option value="CLOSE">Only when a candle closes</option>
            </select>
          </label>
          {form.check === "CLOSE" && (
            <label className="block">
              <span className={LABEL}>Candle</span>
              <select className={FIELD} value={form.timeframe} onChange={(e) => set({ timeframe: Number(e.target.value) })}>
                {TIMEFRAMES.map((t) => <option key={t.minutes} value={t.minutes}>{t.label}</option>)}
              </select>
            </label>
          )}
        </div>
      )}

      <div className="space-y-space-1 rounded-control border border-border bg-surface-2 p-space-3" aria-live="polite" data-preview>
        <p className="font-semibold text-fg" data-preview-words>{words || "Tick the positions and enter a level to see what happens."}</p>
        {now !== null && (
          <p className="text-t13 text-fg-muted" data-preview-now>
            {rupees ? `Combined profit / loss now: ${now < 0 ? "−" : ""}${formatINR(Math.abs(now))}` : `Now: ${formatNumber(now, 2)}`}
          </p>
        )}
        {checking && <p className="text-t12 text-fg-muted">Checking…</p>}
        <p className="text-t12 text-fg-muted">Nothing is sent now. When the level is reached, the ticked positions close together at the current price.</p>
      </div>

      {(reasons.length > 0 || (errors.length > 0 && (form.sl.trim() || form.target.trim()))) && (
        <ul className="space-y-space-1 text-t13 text-fg" role="alert" data-combined-errors>
          {[...new Set([...errors, ...reasons])].map((r) => (
            <li key={r} className="flex items-start gap-space-1"><TriangleAlert size={14} aria-hidden className="mt-px shrink-0 text-warn" />{r}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

const TRAIL_CHOICES: { key: TrailKind; label: string; hint: string }[] = [
  { key: "NONE", label: "No trailing", hint: "The stop stays where you set it" },
  { key: "LOCK", label: "Lock a gain", hint: "After a set gain, the stop moves to keep part of it" },
  { key: "TRAIL", label: "Trail step by step", hint: "Every set move your way shifts the stop" },
];

/** CX2: the optional "Trailing" part of the sheet. Empty boxes until the user types; nothing is suggested. */
function TrailingPartV2({ form, set }: { form: CombinedForm; set: (patch: Partial<CombinedForm>) => void }) {
  const kind = form.trailKind ?? "NONE";
  const labels = kind === "NONE" ? null : trailLabels(kind, form.measure);
  return (
    <fieldset className="space-y-space-1" data-trailing>
      <legend className={LABEL}>Trailing (optional)</legend>
      <div className="grid grid-cols-1 gap-space-1 sm:grid-cols-3">
        {TRAIL_CHOICES.map((c) => (
          <label key={c.key} className={cx("flex min-h-11 cursor-pointer items-start gap-space-2 rounded-control border px-space-3 py-space-2",
            kind === c.key ? "border-brand bg-surface-2" : "border-border")} data-trail-kind={c.key}>
            <input type="radio" name="combined-trailing" checked={kind === c.key} className="mt-px h-4 w-4 accent-brand"
              onChange={() => set({ trailKind: c.key, trailA: "", trailB: "" })} />
            <span className="min-w-0">
              <span className="block text-t13 font-semibold">{c.label}</span>
              <span className="block text-t12 text-fg-muted">{c.hint}</span>
            </span>
          </label>
        ))}
      </div>
      {labels && (
        <div className="grid grid-cols-1 gap-space-3 pt-space-2 sm:grid-cols-2">
          <label className="block">
            <span className={LABEL}>{labels.a}</span>
            <input className={FIELD} inputMode="decimal" value={form.trailA ?? ""} onChange={(e) => set({ trailA: e.target.value })} data-testid="combined-trail-a" autoComplete="off" />
          </label>
          <label className="block">
            <span className={LABEL}>{labels.b}</span>
            <input className={FIELD} inputMode="decimal" value={form.trailB ?? ""} onChange={(e) => set({ trailB: e.target.value })} data-testid="combined-trail-b" autoComplete="off" />
          </label>
        </div>
      )}
      {labels && <p className="text-t12 text-fg-muted">The stop only moves your way, never back.</p>}
    </fieldset>
  );
}

// --- The sheet -----------------------------------------------------------------------------------------------------------

/** The sheet on a phone: from the bottom, at most 85% of the screen, scrolling inside. */
function BottomSheet(props: ComponentProps<typeof Dialog>) { return <Sheet side="bottom" {...props} />; }

const EMPTY_FORM: CombinedForm = { ticked: [], measure: "MTM_INR", sl: "", target: "", check: "TOUCH", timeframe: 15 };

/** The sheet's first form: every leg ticked with its default measure, or the exit being edited. Pure. */
export function startForm(legs: readonly SheetLeg[], editing: CombinedExit | null): CombinedForm {
  if (editing) {
    return {
      ...EMPTY_FORM, ticked: editing.legs.map((l) => l.trade_id), measure: editing.measure,
      sl: editing.sl_value !== null ? String(editing.sl_value) : "", target: editing.target_value !== null ? String(editing.target_value) : "",
      check: editing.check_mode, timeframe: editing.timeframe_minutes ?? 15, ...editTrail(editing),
    };
  }
  // Every open position of one kind ticked (PAPER and LIVE never share one exit): the kind of the first one.
  const same = legs.filter((l) => (l.mode ?? "") === (legs[0]?.mode ?? ""));
  return { ...EMPTY_FORM, ticked: same.map((l) => l.id), measure: defaultMeasure(same) };
}

/** CX2: the trailing of an exit being edited, as the form holds it. Pure. */
function editTrail(x: CombinedExit): Pick<CombinedForm, "trailKind" | "trailA" | "trailB"> {
  const t = x.trailing ?? null;
  if (x.trail_kind === "LOCK" && t) return { trailKind: "LOCK", trailA: String(t.after ?? ""), trailB: String(t.lock ?? "") };
  if (x.trail_kind === "TRAIL" && t) return { trailKind: "TRAIL", trailA: String(t.step ?? ""), trailB: String(t.by ?? "") };
  return { trailKind: "NONE", trailA: "", trailB: "" };
}

/** An exit being edited lists its own legs (some may have closed since), fixed. */
const editLegs = (x: CombinedExit): SheetLeg[] =>
  x.legs.map((l) => ({ id: l.trade_id, symbol: l.symbol, short: l.role === "SHORT", underlying: null, spotSymbol: x.spot_symbol, spotExchange: null }));

export function CombinedExitSheetV2({ open, legs, editing, onClose, onDone, api = combinedApi }: {
  open: boolean; legs: readonly SheetLeg[]; editing: CombinedExit | null;
  onClose: () => void; onDone: (text: string, ok: boolean) => void; api?: CombinedApi;
}) {
  const shown = editing ? editLegs(editing) : legs;
  // A phone gets a bottom sheet (its own scroll, the confirm always on screen); a wider screen the centred dialog.
  const phone = useMediaQuery("(max-width: 639px)");
  const [form, setForm] = useState<CombinedForm>(() => startForm(shown, editing));
  const [preview, setPreview] = useState<CombinedPreview | null>(null);
  const [checking, setChecking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<string>(newRef());
  const opening = `${open}:${editing?.id ?? "new"}`;
  // A new opening starts from the positions on screen (or the exit being edited) with a new reference.
  useEffect(() => {
    if (!open) return;
    setForm(startForm(editing ? editLegs(editing) : legs, editing));
    setPreview(null); setError(null); ref.current = newRef();
  }, [opening]);                                                         // eslint-disable-line react-hooks/exhaustive-deps

  const errors = formErrors(form, shown);
  const bodyKey = JSON.stringify(bodyOf(form, shown, "x"));
  // The server's own sentence, value now and distance, a moment after the last change (a new set only).
  useEffect(() => {
    if (!open || editing || errors.length) { setPreview(null); return undefined; }
    let live = true;
    setChecking(true);
    const t = setTimeout(() => {
      api.preview(bodyOf(form, shown, ref.current)).then((p) => { if (live) setPreview(p); }, () => { if (live) setPreview(null); })
        .finally(() => { if (live) setChecking(false); });
    }, 400);
    return () => { live = false; clearTimeout(t); setChecking(false); };
  }, [open, editing, bodyKey]);                                          // eslint-disable-line react-hooks/exhaustive-deps

  if (!open) return null;
  const Shell = phone ? BottomSheet : Dialog;
  const onlyTarget = amount(form.sl) === null && amount(form.target) !== null;
  const confirm = editing ? "Save changes" : onlyTarget ? "Set combined target" : "Set combined stop-loss";
  const blocked = errors.length > 0 || (!!preview && !preview.ok) || busy;

  async function go() {
    setBusy(true); setError(null);
    try {
      const saved = editing
        ? await api.edit(editing.id, { version: editing.version, sl_value: amount(form.sl), target_value: amount(form.target), trailing: trailOf(form),
          ...(editing.measure === "SPOT" ? { check_mode: form.check, timeframe_minutes: form.check === "CLOSE" ? form.timeframe : null } : {}) })
        : await api.create(bodyOf(form, shown, ref.current));
      onDone(`${editing ? "Combined exit changed" : "Combined exit set"}: ${saved.words}`, true);
    } catch (e) {
      setError(failText(editing ? "The combined exit was not changed." : "The combined exit was not set.", e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell open onOpenChange={(o) => { if (!o) onClose(); }} title={editing ? "Change combined exit" : "Set combined stop-loss"}
      description={<span className="text-t14 text-fg-muted">One stop-loss or target for several positions together. They close as one group, sold legs first.</span>}
      footer={(
        <>
          <button type="button" onClick={onClose} className={BUTTON_SECONDARY}>Cancel</button>
          <button type="button" onClick={() => void go()} disabled={blocked} data-confirm data-testid="combined-confirm" className={BUTTON_PRIMARY}>
            {busy ? "Saving…" : confirm}
          </button>
        </>
      )}>
      <CombinedExitFormV2 view={{ legs: shown, form, editing: !!editing, preview, checking }} onChange={setForm} />
      {error && <p role="alert" className="mt-space-3 text-t13 text-warn" data-combined-failed>{error}</p>}
    </Shell>
  );
}

// --- On the positions panel ----------------------------------------------------------------------------------------------

/**
 * The button, the chips and the sheet, for one positions panel. `positions`: his open positions on screen; `facts`: what
 * the F&O contracts are (for the spot of a spread). `onNote`: the panel's notice line.
 */
export function CombinedExitBarV2({ positions, facts, exits, onChanged, onNote, api = combinedApi }: {
  positions: readonly TradeRecord[]; facts?: ReadonlyMap<string, ContractFacts>; exits: readonly CombinedExit[];
  onChanged: () => void; onNote: (text: string, ok: boolean) => void; api?: CombinedApi;
}) {
  const legs = useMemo(() => sheetLegs(positions, facts), [positions, facts]);
  const [sheet, setSheet] = useState<{ editing: CombinedExit | null } | null>(null);
  const live = active(exits);
  if (!legs.length && !live.length) return null;

  async function remove(x: CombinedExit) {
    if (!window.confirm("Remove this combined exit? The positions stay open with their own stop-loss and target.")) return;
    try {
      await api.cancel(x.id);
      onNote("Combined exit removed. The positions stay open.", true);
    } catch (e) {
      onNote(failText("The combined exit was not removed.", e), false);
    }
    onChanged();
  }

  return (
    <div className="space-y-space-2" data-block="combined-exits">
      <div className="flex flex-wrap items-center gap-space-2">
        {legs.length > 0 && (
          <button type="button" className={BUTTON_SECONDARY} onClick={() => setSheet({ editing: null })} data-testid="combined-open">
            <ShieldCheck size={16} aria-hidden />Set combined stop-loss
          </button>
        )}
      </div>
      {live.length > 0 && (
        <ul className="flex flex-wrap gap-space-2" aria-label="Combined exits">
          {live.map((x) => <CombinedExitChipV2 key={x.id} exit={x} onEdit={(e) => setSheet({ editing: e })} onRemove={(e) => void remove(e)} />)}
        </ul>
      )}
      <CombinedExitSheetV2 open={!!sheet} legs={legs} editing={sheet?.editing ?? null} api={api} onClose={() => setSheet(null)}
        onDone={(text, ok) => { setSheet(null); onNote(text, ok); onChanged(); }} />
    </div>
  );
}

// --- CX-F2: "Exit short now" -----------------------------------------------------------------------------------------------

/** The confirm of the CRITICAL notice's "Exit short now": this one position closes at the current price through its own
 * exit (`exit-leg`, never blocked). A failure is a plain line. */
export function ExitShortSheetV2({ trade, onClose, onDone, api = groupExitApi }: {
  trade: Pick<TradeRecord, "id" | "symbol"> | null; onClose: () => void; onDone: (text: string, ok: boolean) => void; api?: GroupExitApi;
}) {
  const [busy, setBusy] = useState(false);
  // A link that names a bought leg protecting an open sold one: the server's warning shows and the next tap confirms it.
  const [warning, setWarning] = useState<string | null>(null);
  if (!trade) return null;
  const copy = exitShortCopy(trade.symbol);
  const close = () => { setWarning(null); onClose(); };
  async function go() {
    if (!trade || busy) return;                                          // one tap sends one exit
    setBusy(true);
    const step = await runExit({ kind: "leg", tradeId: trade.id, symbol: trade.symbol, hedge: false, warning: null }, warning !== null, api);
    setBusy(false);
    if (step.kind === "warn") { setWarning(step.warning); return; }
    setWarning(null);
    onDone(step.text, step.ok);
  }
  return (
    <Dialog open onOpenChange={(o) => { if (!o) close(); }} title={copy.title}
      description={<span className="text-t14 text-fg" data-consequence>{copy.body}</span>}
      footer={(
        <>
          <button type="button" onClick={close} className={BUTTON_SECONDARY}>Not now</button>
          <button type="button" onClick={() => void go()} aria-busy={busy} data-confirm data-testid="exit-short-confirm" className={BUTTON_PRIMARY}>
            {busy ? "Exiting…" : warning ? "Exit it anyway" : copy.confirm}
          </button>
        </>
      )}>
      {warning ? (
        <p className="flex items-start gap-space-2 rounded-control border border-border bg-surface-2 p-space-3 text-t14 text-fg" role="alert" data-hedge-warning>
          <TriangleAlert size={16} aria-hidden className="mt-px shrink-0 text-fg-muted" /><span>{warning}</span>
        </p>
      ) : (
        <p className="text-t13 text-fg-muted">The bought legs that protect it stay open; exit them after this one.</p>
      )}
    </Dialog>
  );
}
