import { Check, ImagePlus, Trash2, X } from "lucide-react";
import { AskAiAbout } from "../copilot/AskAiAbout";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { errorSentence } from "../api/errors";
import { Sheet } from "../components/primitives/Dialog";
import { useToast } from "../components/Toast";
import { LoadingRowsV2 } from "../dashboard/CardV2";
import { BUTTON_PRIMARY, BUTTON_SECONDARY } from "../trading/PartsV2";
import { chipClass } from "../ui/Chip";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { ErrorState } from "../ui/ErrorState";
import { formatDateTimeIST, formatINR, formatNumber } from "../ui/format";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";
import type { JournalApi } from "./api";
import { TAG_HELP, minutesText, tagLabel } from "./format";
import { OwnImageV2, PnlV2, RV2, TEXTAREA } from "./JournalPartsV2";
import type { EntryKind, JournalEntryFull, TagStatus } from "./types";

/**
 * M62 JN-3: the entry editor, as a sheet (right side on a wider screen, from the bottom on a phone). The plan is shown
 * as recorded (read-only); the execution as filled; his notes, his own tags, his rule checklist (yes / no) and his
 * images are his to change at any time. The detector's tags can be confirmed or dismissed. Nothing here touches an order.
 */

export interface EntryTarget { kind: EntryKind; id: number }

const NOTES_MAX = 5000;

function Section({ title, children, testId }: { title: string; children: ReactNode; testId?: string }) {
  return (
    <section className="border-t border-subtle pt-space-4 first:border-0 first:pt-0" data-sheet-section={testId}>
      <h3 className="mb-space-2 text-t14 font-semibold text-fg">{title}</h3>
      {children}
    </section>
  );
}

function Pairs({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-space-3 gap-y-space-1 text-t14">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-fg-muted">{k}</dt>
          <dd className="m-0 text-right font-tabular text-fg">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

type EditorApi = Pick<JournalApi, "saveNotes" | "saveUserTags" | "saveChecklist" | "decideTag" | "attach" | "removeAttachment">;

/** What the editor's controls do: each call saves one change and resolves to the entry as it now is. */
export function entryActions(api: EditorApi, entry: JournalEntryFull) {
  return {
    saveNotes: async (notes: string) => ({ ...entry, notes: (await api.saveNotes(entry.kind, entry.id, notes)).notes }),
    toggleTag: async (tag: string) => {
      const next = entry.user_tags.includes(tag) ? entry.user_tags.filter((t) => t !== tag) : [...entry.user_tags, tag];
      return { ...entry, user_tags: (await api.saveUserTags(entry.kind, entry.id, next)).user_tags };
    },
    answer: async (rule: string, followed: boolean | null) =>
      ({ ...entry, checklist: (await api.saveChecklist(entry.kind, entry.id, [{ rule, followed }])).checklist }),
    decide: async (tagId: number, status: TagStatus) => {
      const t = await api.decideTag(tagId, status);
      return { ...entry, tags: entry.tags.map((x) => (x.id === t.id ? t : x)) };
    },
    upload: async (file: File) => ({ ...entry, attachments: [...entry.attachments, await api.attach(entry.kind, entry.id, file, file.name)] }),
    removeImage: async (id: number) => {
      await api.removeAttachment(id);
      return { ...entry, attachments: entry.attachments.filter((a) => a.id !== id) };
    },
  };
}

const price = (v: number | null | undefined) => (v == null ? "–" : formatNumber(v, 2));

export function EntryEditorV2({ entry, api, customTags, onChanged, onRemoved, extra }: {
  entry: JournalEntryFull; api: JournalApi; customTags: readonly string[]; onChanged: (e: JournalEntryFull) => void; onRemoved: () => void;
  extra?: (e: JournalEntryFull) => ReactNode;
}) {
  const toast = useToast();
  const [notes, setNotes] = useState(entry.notes ?? "");
  const [busy, setBusy] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => { setNotes(entry.notes ?? ""); }, [entry.kind, entry.id, entry.notes]);

  async function run(what: string, job: () => Promise<void>, done?: string) {
    setBusy(what);
    try { await job(); if (done) toast.success(done); } catch (e) { toast.error(errorSentence(e)); } finally { setBusy(null); }
  }
  const act = entryActions(api, entry);
  const saveNotes = () => run("notes", async () => onChanged(await act.saveNotes(notes)), "Notes saved.");
  const toggleTag = (tag: string) => run("tags", async () => onChanged(await act.toggleTag(tag)));
  const answer = (rule: string, followed: boolean | null) => run("checklist", async () => onChanged(await act.answer(rule, followed)));
  const decide = (tagId: number, status: TagStatus) => run("decide", async () => onChanged(await act.decide(tagId, status)));
  const upload = (file: File) => run("upload", async () => onChanged(await act.upload(file)), "Image added.");
  const removeImage = (id: number) => run(`img-${id}`, async () => onChanged(await act.removeImage(id)), "Image removed.");

  const p = entry.plan_view;
  const x = entry.execution;
  const exc = entry.excursions;
  return (
    <div className="space-y-space-4" data-entry-editor={`${entry.kind}-${entry.id}`}>
      <div className="flex flex-wrap items-center justify-between gap-space-2">
        <span className="font-tabular text-t13 text-fg-muted">{formatDateTimeIST(entry.entry_time)}</span>
        <span className="flex items-center gap-space-3"><PnlV2 value={entry.pnl} paise /><RV2 value={entry.r_multiple} /></span>
        {/* M72 PR-3: the Copilot beside the page, about this journal entry (a trade's entry: its tags too) */}
        <AskAiAbout ctx={{ symbol: entry.symbol, object: entry.kind === "trade" ? { kind: "journal", id: entry.id } : undefined, page: "journal" }} />
      </div>

      <Section title="Plan (as recorded)" testId="plan">
        <Pairs rows={[
          ["Setup", p.setup ?? "–"], ["Timeframe", p.timeframe ?? "–"], ["Planned entry", price(p.entry)],
          ["Invalidation", price(p.invalidation)], ["Target", price(p.target)],
        ]} />
        {p.area && p.area.length > 0 && <p className="mt-space-2 text-t13 text-fg-muted" data-plan-area>{p.area.join(" · ")}</p>}
      </Section>

      <Section title="Execution" testId="execution">
        <Pairs rows={[
          ["Side", entry.direction === "LONG" ? "Long" : entry.direction === "SHORT" ? "Short" : entry.direction],
          ["Entry fill", price(x.entry_price)], ["Quantity", formatNumber(x.quantity, Number.isInteger(x.quantity) ? 0 : 2)],
          ["Exit fill", x.exit_price == null ? "Open" : price(x.exit_price)], ["Exit reason", x.exit_reason ?? "–"],
          ["Slippage", x.slippage == null ? "–" : formatNumber(x.slippage, 2)], ["Charges", formatINR(entry.costs.charges, { paise: true })],
          ["Held", minutesText(entry.holding_minutes)],
          ...(exc ? [["MAE / MFE", `${exc.mae_r == null ? "–" : `${formatNumber(exc.mae_r, 2)}R`} / ${exc.mfe_r == null ? "–" : `${formatNumber(exc.mfe_r, 2)}R`}`] as [string, ReactNode]] : []),
        ]} />
      </Section>

      <Section title="Notes" testId="notes">
        <textarea className={`${TEXTAREA} h-28`} value={notes} maxLength={NOTES_MAX} aria-label="Notes"
          onChange={(e) => setNotes(e.target.value)} data-notes />
        <div className="mt-space-2 flex justify-end">
          <button type="button" className={BUTTON_PRIMARY} disabled={busy !== null || notes === (entry.notes ?? "")} onClick={saveNotes} data-save-notes>
            {busy === "notes" ? "Saving…" : "Save notes"}
          </button>
        </div>
      </Section>

      <Section title="Tags" testId="tags">
        {customTags.length === 0 && entry.user_tags.length === 0
          ? <p className="text-t13 text-fg-muted">Add your own tags in Settings.</p>
          : (
            <div className="flex flex-wrap gap-space-2" role="group" aria-label="Your tags">
              {[...new Set([...customTags, ...entry.user_tags])].map((t) => {
                const on = entry.user_tags.includes(t);
                return (
                  <button key={t} type="button" aria-pressed={on} disabled={busy !== null} onClick={() => toggleTag(t)} data-own-tag={t}
                    className={`${chipClass(on ? { tone: "brand", variant: "solid" } : { variant: "outline" })} min-h-9 gap-space-1`}>
                    {on && <Check size={12} aria-hidden />}{t}
                  </button>
                );
              })}
            </div>
          )}
        {entry.tags.length > 0 && (
          <ul className="mt-space-3 space-y-space-2" aria-label="Found by the journal">
            {entry.tags.map((t) => (
              <li key={t.id} className="flex flex-wrap items-center gap-space-2 text-t14" data-detector-tag={t.tag}>
                <span className={`${chipClass({ tone: t.status === "DISMISSED" ? "neutral" : "warn" })} ${t.status === "DISMISSED" ? "line-through" : ""}`}
                  title={TAG_HELP[t.tag] ?? ""}>{tagLabel(t.tag)}</span>
                <span className="text-t12 text-fg-muted">{t.status === "SUGGESTED" ? "Suggested" : t.status === "CONFIRMED" ? "Confirmed" : "Dismissed"}</span>
                {t.status !== "CONFIRMED" && <button type="button" className="min-h-9 text-t13 text-brand underline" onClick={() => decide(t.id, "CONFIRMED")}>Confirm</button>}
                {t.status !== "DISMISSED" && <button type="button" className="min-h-9 text-t13 text-fg-muted underline" onClick={() => decide(t.id, "DISMISSED")}>Dismiss</button>}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="My rules" testId="checklist">
        {entry.checklist.length === 0 ? <p className="text-t13 text-fg-muted">Add your own rules in Settings.</p> : (
          <ul className="space-y-space-2">
            {entry.checklist.map((c) => (
              <li key={c.rule} className="flex items-center justify-between gap-space-3 text-t14" data-rule={c.rule}>
                <span className={`min-w-0 ${c.retired ? "text-fg-muted" : "text-fg"}`}>{c.rule}</span>
                <span className="flex shrink-0 gap-space-1" role="group" aria-label={c.rule}>
                  {([true, false] as const).map((v) => (
                    <button key={String(v)} type="button" aria-pressed={c.followed === v} disabled={busy !== null || c.retired}
                      onClick={() => answer(c.rule, c.followed === v ? null : v)} data-answer={v ? "yes" : "no"}
                      className={`inline-flex min-h-9 min-w-12 items-center justify-center gap-space-1 rounded-control border px-space-2 text-t13 font-semibold ${
                        c.followed === v ? (v ? "border-up bg-up-tint text-fg" : "border-down bg-down-tint text-fg") : "border-border text-fg-muted"}`}>
                      {v ? <Check size={12} aria-hidden /> : <X size={12} aria-hidden />}{v ? "Yes" : "No"}
                    </button>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section title="Charts" testId="charts">
        <div className="grid gap-space-3 md:grid-cols-1">
          <figure>
            <figcaption className="mb-space-1 text-t13 text-fg-muted">At entry</figcaption>
            <OwnImageV2 api={api} path={entry.charts.entry} label={`${entry.symbol} at entry`} missing="No stored candles for this chart." />
          </figure>
          <figure>
            <figcaption className="mb-space-1 text-t13 text-fg-muted">At exit</figcaption>
            <OwnImageV2 api={api} path={entry.charts.exit} label={`${entry.symbol} at exit`} missing={entry.exit_time ? "No stored candles for this chart." : "Still open."} />
          </figure>
        </div>
      </Section>

      <Section title="Images" testId="images">
        {entry.attachments.length > 0 && (
          <ul className="mb-space-3 grid grid-cols-2 gap-space-2">
            {entry.attachments.map((a) => (
              <li key={a.id} className="relative" data-attachment={a.id}>
                <OwnImageV2 api={api} path={`/journal/attachments/${a.id}`} label="Your image" missing="Image not available." />
                <button type="button" aria-label="Remove image" disabled={busy !== null} onClick={() => removeImage(a.id)} data-remove-image={a.id}
                  className="absolute right-space-1 top-space-1 inline-flex h-9 w-9 items-center justify-center rounded-pill border border-border bg-surface-1 text-fg-muted hover:text-down">
                  <Trash2 size={14} aria-hidden />
                </button>
              </li>
            ))}
          </ul>
        )}
        <input ref={fileRef} type="file" accept="image/png,image/jpeg" className="sr-only" aria-label="Choose an image" data-upload
          onChange={(e) => { const f = e.target.files?.[0]; if (f) upload(f); e.target.value = ""; }} />
        <button type="button" className={BUTTON_SECONDARY} disabled={busy !== null} onClick={() => fileRef.current?.click()}>
          <ImagePlus size={16} aria-hidden />{busy === "upload" ? "Uploading…" : "Add an image"}
        </button>
      </Section>

      {extra?.(entry)}

      {entry.kind === "manual" && (
        <div className="border-t border-subtle pt-space-4">
          <button type="button" className={`${BUTTON_SECONDARY} text-down`} onClick={() => setConfirmRemove(true)} data-remove-manual>
            <Trash2 size={16} aria-hidden />Remove this entry
          </button>
          <ConfirmDialog open={confirmRemove} onOpenChange={setConfirmRemove} title="Remove this entry?" busy={busy === "remove"}
            consequence="The entry and its images are removed from your journal." confirmLabel="Remove"
            note="You can add it again from the list."
            onConfirm={() => run("remove", async () => { await api.removeManual(entry.id); setConfirmRemove(false); onRemoved(); }, "Entry removed.")} />
        </div>
      )}
    </div>
  );
}

type SheetState = { status: "loading" } | { status: "error"; error: unknown } | { status: "ok"; entry: JournalEntryFull };

export function EntrySheetV2({ target, api, customTags, onClose, onChanged, extra }: {
  target: EntryTarget | null; api: JournalApi; customTags: readonly string[]; onClose: () => void; onChanged: () => void;
  extra?: (e: JournalEntryFull) => ReactNode;
}) {
  const phone = useMediaQuery(PHONE_QUERY);
  const [state, setState] = useState<SheetState>({ status: "loading" });
  const [round, setRound] = useState(0);
  useEffect(() => {
    if (!target) return undefined;
    let alive = true;
    setState({ status: "loading" });
    api.entry(target.kind, target.id).then((entry) => { if (alive) setState({ status: "ok", entry }); })
      .catch((error) => { if (alive) setState({ status: "error", error }); });
    return () => { alive = false; };
  }, [api, target, round]);
  const title = state.status === "ok"
    ? <span className="flex flex-wrap items-center gap-space-2">{state.entry.symbol}
      {state.entry.manual && <span className={chipClass({ tone: "warn" })}>Outside ATP</span>}</span>
    : "Journal entry";
  return (
    <Sheet open={target !== null} onOpenChange={(o) => { if (!o) onClose(); }} side={phone ? "bottom" : "right"} title={title}>
      {state.status === "loading" && <LoadingRowsV2 rows={5} />}
      {state.status === "error" && <ErrorState message={state.error} onRetry={() => setRound((r) => r + 1)} variant="inline" />}
      {state.status === "ok" && (
        <EntryEditorV2 entry={state.entry} api={api} customTags={customTags} extra={extra}
          onChanged={(entry) => { setState({ status: "ok", entry }); onChanged(); }}
          onRemoved={() => { onChanged(); onClose(); }} />
      )}
    </Sheet>
  );
}
