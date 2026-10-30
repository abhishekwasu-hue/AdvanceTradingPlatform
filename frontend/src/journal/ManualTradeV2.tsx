import { useState } from "react";
import { errorSentence } from "../api/errors";
import { Sheet } from "../components/primitives/Dialog";
import { useToast } from "../components/Toast";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, FIELD, LABEL } from "../trading/PartsV2";
import { SubTabs } from "../ui/SubTabs";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";
import type { JournalApi } from "./api";
import { TEXTAREA } from "./JournalPartsV2";
import { EMPTY_MANUAL, manualBody, manualProblems, type ManualForm } from "./journalModel";
import type { JournalEntryFull } from "./types";

/**
 * M62 JN-3: "Add a trade done outside ATP" - his own record of a trade taken elsewhere. Only symbol, side, quantity,
 * entry time and entry price are needed; the rest is his to fill or leave empty. Journal data only: no order, no
 * position, no broker call; the entry is labelled "Outside ATP" everywhere it shows.
 */

type Field = keyof Omit<ManualForm, "direction">;

const FIELDS: { key: Field; label: string; type?: string; required?: boolean; wide?: boolean }[] = [
  { key: "symbol", label: "Symbol", required: true },
  { key: "quantity", label: "Quantity", type: "number", required: true },
  { key: "entry_time", label: "Entry time (IST)", type: "datetime-local", required: true },
  { key: "entry_price", label: "Entry price", type: "number", required: true },
  { key: "stop_loss", label: "Invalidation", type: "number" },
  { key: "target", label: "Target", type: "number" },
  { key: "exit_time", label: "Exit time (IST)", type: "datetime-local" },
  { key: "exit_price", label: "Exit price", type: "number" },
  { key: "exit_reason", label: "Exit reason" },
  { key: "charges", label: "Charges (₹)", type: "number" },
  { key: "setup", label: "Setup" },
  { key: "timeframe", label: "Timeframe" },
  { key: "notes", label: "Notes", wide: true },
];

/** Checks the form, then sends it: the problems (nothing sent) or the saved entry. */
export async function submitManual(api: Pick<JournalApi, "addManual">, form: ManualForm, now?: Date): Promise<{ problems: string[]; saved?: JournalEntryFull }> {
  const problems = manualProblems(form, now);
  if (problems.length) return { problems };
  return { problems: [], saved: await api.addManual(manualBody(form)) };
}

export function ManualTradeFormV2({ api, onSaved, onCancel, now }: {
  api: Pick<JournalApi, "addManual">; onSaved: (e: JournalEntryFull) => void; onCancel: () => void; now?: Date;
}) {
  const toast = useToast();
  const [form, setForm] = useState<ManualForm>(EMPTY_MANUAL);
  const [shown, setShown] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const set = (key: keyof ManualForm, value: string) => setForm((f) => ({ ...f, [key]: value }));
  async function save() {
    setBusy(true);
    try {
      const r = await submitManual(api, form, now);
      setShown(r.problems);
      if (!r.saved) return;
      toast.success("Added to your journal.");
      setForm(EMPTY_MANUAL);
      onSaved(r.saved);
    } catch (e) { toast.error(errorSentence(e)); } finally { setBusy(false); }
  }
  return (
    <form className="space-y-space-4" data-manual-form onSubmit={(e) => { e.preventDefault(); void save(); }} noValidate>
      <SubTabs label="Side" value={form.direction} onChange={(v) => set("direction", v)} size="sm"
        items={[{ key: "LONG", label: "Long" }, { key: "SHORT", label: "Short" }]} />
      <div className="grid grid-cols-2 gap-space-3">
        {FIELDS.map((f) => (
          <label key={f.key} className={`min-w-0 ${f.wide ? "col-span-2" : ""}`}>
            <span className={LABEL}>{f.label}{f.required && <span aria-hidden className="text-down">*</span>}</span>
            {f.key === "notes"
              ? <textarea className={`${TEXTAREA} h-20`} value={form.notes} onChange={(e) => set("notes", e.target.value)} maxLength={5000} />
              : <input className={FIELD} type={f.type ?? "text"} inputMode={f.type === "number" ? "decimal" : undefined} step={f.type === "number" ? "any" : undefined}
                value={form[f.key]} onChange={(e) => set(f.key, e.target.value)} aria-required={f.required || undefined} data-manual={f.key} />}
          </label>
        ))}
      </div>
      {shown.length > 0 && (
        <ul role="alert" className="space-y-space-1 rounded-panel border border-warn/40 bg-warn-tint px-space-4 py-space-3 text-t14 text-fg" data-manual-problems>
          {shown.map((p) => <li key={p}>{p}</li>)}
        </ul>
      )}
      <div className="flex justify-end gap-space-2">
        <button type="button" className={BUTTON_SECONDARY} onClick={onCancel}>Cancel</button>
        <button type="submit" className={BUTTON_PRIMARY} disabled={busy} data-manual-save>{busy ? "Saving…" : "Add to journal"}</button>
      </div>
    </form>
  );
}

export function ManualTradeSheetV2({ open, api, onClose, onSaved }: {
  open: boolean; api: Pick<JournalApi, "addManual">; onClose: () => void; onSaved: (e: JournalEntryFull) => void;
}) {
  const phone = useMediaQuery(PHONE_QUERY);
  return (
    <Sheet open={open} onOpenChange={(o) => { if (!o) onClose(); }} side={phone ? "bottom" : "right"} title="Add a trade done outside ATP">
      {open && <ManualTradeFormV2 api={api} onCancel={onClose} onSaved={(e) => { onSaved(e); onClose(); }} />}
    </Sheet>
  );
}
