import { useEffect, useState } from "react";
import { errorSentence } from "../api/errors";
import { useToast } from "../components/Toast";
import { LoadingRowsV2 } from "../dashboard/CardV2";
import { BUTTON_SECONDARY, FIELD, LABEL } from "../trading/PartsV2";
import { chipClass } from "../ui/Chip";
import { Combobox } from "../ui/Combobox";
import { formatTimeIST } from "../ui/format";
import type { JournalApi } from "./api";
import { tagLabel } from "./format";
import type { Checkin, CheckinBody, EntryKind, PlanAnswer } from "./types";

/**
 * P2-8 J2: the 20-second check-in on a journal entry - his feeling and confidence before, quick notes while open, his
 * feeling after, "Did I follow my plan?", the mistake (if any) and one line. Every field optional, one tap each, saved
 * at once; nothing is required and nothing here touches an order. Private: only he sees it.
 */

export const PLAN_LABELS: Record<PlanAnswer, string> = { YES: "Yes", PARTLY: "Partly", NO: "No" };

export function mistakeLabel(value: string): string {
  return value === "other" ? "Other" : tagLabel(value);
}

type CheckinApi = Pick<JournalApi, "checkin" | "saveCheckin" | "addCheckinNote">;

function Chips<T extends string | number>({ label, options, value, onPick, text, testId }: {
  label: string; options: readonly T[]; value: T | null; onPick: (v: T | null) => void; text: (v: T) => string; testId: string;
}) {
  return (
    <div className="min-w-0">
      <span className={LABEL}>{label}</span>
      <div className="flex flex-wrap gap-space-2" role="group" aria-label={label} data-checkin-field={testId}>
        {options.map((o) => (
          <button key={String(o)} type="button" aria-pressed={value === o} onClick={() => onPick(value === o ? null : o)}
            className={`${chipClass(value === o ? { tone: "brand", variant: "solid" } : { variant: "outline" })} min-h-9`}>
            {text(o)}
          </button>
        ))}
      </div>
    </div>
  );
}

export function CheckinFormV2({ data, closed, onSave, onNote, busy }: {
  data: Checkin; closed: boolean; busy: boolean; onSave: (body: CheckinBody) => void; onNote: (text: string) => void;
}) {
  const [lesson, setLesson] = useState(data.lesson ?? "");
  const [setup, setSetup] = useState(data.setup_name ?? data.setup_name_suggested ?? "");
  const [note, setNote] = useState("");
  useEffect(() => { setLesson(data.lesson ?? ""); }, [data.lesson]);
  return (
    <div className="space-y-space-4" data-checkin>
      <Chips label="Before: how did you feel?" options={data.feelings} value={data.feeling_before} text={(f) => f} testId="feeling_before"
        onPick={(v) => onSave({ feeling_before: v })} />
      <Chips label="Confidence (1-5)" options={[1, 2, 3, 4, 5] as const} value={data.confidence as 1 | 2 | 3 | 4 | 5 | null} text={String} testId="confidence"
        onPick={(v) => onSave({ confidence: v })} />
      <div className="min-w-0">
        <span className={LABEL}>While open: a quick note</span>
        {data.notes.length > 0 && (
          <ul className="mb-space-2 space-y-space-1 text-t13 text-fg" data-checkin-notes>
            {data.notes.map((n) => <li key={n.at}><span className="font-tabular text-fg-muted">{formatTimeIST(n.at)}</span> {n.text}</li>)}
          </ul>
        )}
        <div className="flex gap-space-2">
          <input className={FIELD} value={note} maxLength={300} placeholder="e.g. unease after entry" aria-label="Quick note"
            onChange={(e) => setNote(e.target.value)} data-checkin-note-input />
          <button type="button" className={BUTTON_SECONDARY} disabled={busy || !note.trim()} data-checkin-note-add
            onClick={() => { onNote(note); setNote(""); }}>Add</button>
        </div>
      </div>
      {closed && (
        <>
          <Chips label="After: how do you feel?" options={data.feelings} value={data.feeling_after} text={(f) => f} testId="feeling_after"
            onPick={(v) => onSave({ feeling_after: v })} />
          <Chips label="Did I follow my plan?" options={data.plan_answers} value={data.plan_followed} text={(a) => PLAN_LABELS[a]} testId="plan_followed"
            onPick={(v) => onSave({ plan_followed: v })} />
          {data.plan_followed && data.plan_followed !== "YES" && (
            <div className="min-w-0">
              <span aria-hidden className={LABEL}>What went off plan?</span>
              <Combobox label="What went off plan?" value={data.mistake ?? ""} onChange={(v) => onSave({ mistake: v || null })}
                dataAttrs={{ "data-checkin-field": "mistake" }}
                options={[{ value: "", label: "Not set" }, ...data.mistakes.map((m) => ({ value: m, label: mistakeLabel(m) }))]} />
            </div>
          )}
          <label className="block min-w-0">
            <span className={LABEL}>What I would do the same / differently</span>
            <input className={FIELD} value={lesson} maxLength={300} onChange={(e) => setLesson(e.target.value)} data-checkin-field="lesson"
              onBlur={() => { if (lesson !== (data.lesson ?? "")) onSave({ lesson: lesson || null }); }} />
          </label>
        </>
      )}
      <label className="block min-w-0">
        <span className={LABEL}>Setup name</span>
        <input className={FIELD} value={setup} maxLength={100} onChange={(e) => setSetup(e.target.value)} data-checkin-field="setup_name"
          onBlur={() => { if (setup !== (data.setup_name ?? data.setup_name_suggested ?? "")) onSave({ setup_name: setup || null }); }} />
      </label>
      <p className="text-t12 text-fg-muted">Only you see this. Skip any field.</p>
    </div>
  );
}

/** The check-in as a section of the entry sheet: loads, saves each tap at once, shows a toast only on a failure. */
export function CheckinV2({ api, kind, id, closed }: { api: CheckinApi; kind: EntryKind; id: number; closed: boolean }) {
  const toast = useToast();
  const [data, setData] = useState<Checkin | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    api.checkin(kind, id).then((d) => { if (alive) setData(d); }).catch(() => { /* intentional: the section stays hidden */ });
    return () => { alive = false; };
  }, [api, kind, id]);
  if (!data) return <LoadingRowsV2 rows={2} />;
  const run = async (work: () => Promise<Checkin>) => {
    setBusy(true);
    try { setData(await work()); } catch (e) { toast.error(errorSentence(e)); } finally { setBusy(false); }
  };
  return <CheckinFormV2 data={data} closed={closed} busy={busy} onSave={(body) => run(() => api.saveCheckin(kind, id, body))}
    onNote={(text) => run(() => api.addCheckinNote(kind, id, text))} />;
}
