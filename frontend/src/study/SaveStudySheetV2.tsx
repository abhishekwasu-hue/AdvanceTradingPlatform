import { useEffect, useId, useState } from "react";
import { Button, Dialog, Input } from "../components/primitives";
import { cx } from "../components/primitives/cx";
import { saveBlob } from "../journal/journalModel";
import { formatNumber } from "../ui/format";
import { keptCards } from "../fno/checkCardLog";
import { studyApi } from "./api";
import {
  FORMATS, NOTES_MAX, SECTIONS, STUDY_TIMEFRAMES, initialSheet, saveBody, sheetProblem, toggle,
  type DownloadFormat, type SectionId, type SheetState, type StudyPlan,
} from "./model";

/**
 * M77 SX-7 (owner order M77 PD-1): the "Save study" sheet of the Copilot's chart workspace - in the same tab (a
 * dialog over the chart; the address never changes). He picks the timeframes, the sections (all on; he unticks) and the
 * file to download; Save stores the study in My studies and downloads that file. The server reads every number
 * itself; the page sends only the chart's instrument and his own choices and words. Read-only: nothing here places,
 * changes or blocks an order.
 */
const FOCUS = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const ORDER_TF = STUDY_TIMEFRAMES.map((t) => t.id as string);
const ORDER_SECTIONS = SECTIONS.map((s) => s.id) as SectionId[];

export const cvdOn = () => typeof document !== "undefined" && document.documentElement.dataset.cvd === "on";

type Status = { kind: "idle" } | { kind: "saving" } | { kind: "done"; text: string } | { kind: "failed"; text: string };

export function SaveStudySheetV2({ open, onOpenChange, instrument, symbol, openTf, onSaved, plan = null }: {
  open: boolean; onOpenChange: (open: boolean) => void; instrument: string; symbol: string; openTf: string | null; onSaved?: () => void;
  /** M77 SX-6: the trade plan open beside the chart (references only), null when none. */
  plan?: StudyPlan | null;
}) {
  const [s, setS] = useState<SheetState>(() => initialSheet(openTf));
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const id = useId();
  useEffect(() => {
    if (open) {
      setS(initialSheet(openTf));
      setStatus({ kind: "idle" });
    }
  }, [open, openTf]);
  const problem = sheetProblem(s, s.format, openTf);
  const save = async () => {
    if (problem) return;
    setStatus({ kind: "saving" });
    try {
      const saved = await studyApi.save(saveBody(instrument, s, plan, keptCards()));
      const file = await studyApi.download(saved.id, s.format, { tf: openTf ?? undefined, cvd: cvdOn() });
      saveBlob(file.blob, file.filename);
      setStatus({ kind: "done", text: `Saved to My studies. ${file.filename} is downloading.` });
      onSaved?.();
    } catch (e) {
      setStatus({ kind: "failed", text: e instanceof Error && e.message ? e.message : "The study could not be saved. Try again." });
    }
  };
  const label = "text-t13 font-medium text-fg-muted";
  const box = "flex min-h-11 items-center gap-space-2 text-t14 text-fg md:min-h-9";
  return (
    <Dialog open={open} onOpenChange={onOpenChange} title={`Save study: ${symbol}`}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Close</Button>
          <Button variant="primary" onClick={save} disabled={!!problem || status.kind === "saving"} loading={status.kind === "saving"} data-study-save>
            Save and download
          </Button>
        </>
      }>
      <div className="space-y-space-4" data-save-study>
        <fieldset>
          <legend className={label}>Timeframes</legend>
          <div className="mt-space-1 flex flex-wrap gap-x-space-4">
            {STUDY_TIMEFRAMES.map((t) => (
              <label key={t.id} className={box}>
                <input type="checkbox" className="h-5 w-5 accent-brand" checked={s.timeframes.includes(t.id)} data-study-tf={t.id}
                  onChange={() => setS((v) => ({ ...v, timeframes: toggle(v.timeframes, t.id, ORDER_TF) }))} />
                {t.label}
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend className={label}>Download as</legend>
          <div className="mt-space-1 grid gap-space-1 sm:grid-cols-2">
            {FORMATS.map((f) => (
              <label key={f.id} className={cx(box, "items-start py-space-1")}>
                <input type="radio" name={`${id}-format`} className="mt-1 h-5 w-5 accent-brand" checked={s.format === f.id} data-study-format={f.id}
                  onChange={() => setS((v) => ({ ...v, format: f.id as DownloadFormat }))} />
                <span><span className="block">{f.label}</span><span className="block text-t13 text-fg-muted">{f.hint}</span></span>
              </label>
            ))}
          </div>
        </fieldset>
        <fieldset>
          <legend className={label}>Sections</legend>
          <div className="mt-space-1 grid sm:grid-cols-2">
            {SECTIONS.map((x) => (
              <label key={x.id} className={box}>
                <input type="checkbox" className="h-5 w-5 accent-brand" checked={s.sections.includes(x.id)} data-study-section={x.id}
                  onChange={() => setS((v) => ({ ...v, sections: toggle(v.sections, x.id, ORDER_SECTIONS) }))} />
                {x.label}
              </label>
            ))}
          </div>
        </fieldset>
        <Input label="Title (optional)" value={s.title} maxLength={120} onChange={(e) => setS((v) => ({ ...v, title: e.target.value }))} data-study-title />
        <div>
          <label htmlFor={`${id}-notes`} className="block text-xs font-medium text-fg-muted">Your notes (optional)</label>
          <textarea id={`${id}-notes`} rows={4} value={s.notes} onChange={(e) => setS((v) => ({ ...v, notes: e.target.value }))} data-study-notes
            className={cx("mt-1 w-full rounded-md border border-border bg-surface-2 px-3 py-2 text-t14 text-fg", FOCUS)} />
          <p className={cx("text-t13", s.notes.length > NOTES_MAX ? "text-down" : "text-fg-muted")}>{formatNumber(s.notes.length)} / {formatNumber(NOTES_MAX)}</p>
        </div>
        {problem && <p className="text-t13 text-fg-muted" data-study-problem>{problem}</p>}
        {status.kind === "done" && <p role="status" className="text-t13 text-fg" data-study-status="done">{status.text}</p>}
        {status.kind === "failed" && <p role="alert" className="text-t13 text-down" data-study-status="failed">{status.text}</p>}
      </div>
    </Dialog>
  );
}
