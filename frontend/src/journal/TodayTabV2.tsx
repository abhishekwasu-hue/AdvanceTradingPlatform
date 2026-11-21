import { useEffect, useState } from "react";
import { errorSentence } from "../api/errors";
import { useToast } from "../components/Toast";
import { CardV2, LoadingRowsV2 } from "../dashboard/CardV2";
import { BUTTON_PRIMARY, LABEL } from "../trading/PartsV2";
import { Combobox } from "../ui/Combobox";
import { ErrorState } from "../ui/ErrorState";
import type { JournalApi } from "./api";
import { GoalsListV2, IfThenListV2 } from "./PlansV2";
import { LastGoodV2 } from "./ProofV2";
import type { Load } from "./ReviewsV2";
import type { EntryKind, JournalDelivery, JournalToday } from "./types";

/**
 * P2-8 J6: the Journal's Today tab - his if-then plans to re-read before the open, a plan a recent stop hit / loss
 * brings back, his goals' progress, his last good decision and (after a loss) how often he followed his plan. His own
 * words and his own numbers only. The waiting check-ins and the day's card sit under it (JournalPageV2).
 */
export function TodayTabV2({ state, onRetry, onOpen }: { state: Load<JournalToday>; onRetry: () => void; onOpen: (kind: EntryKind, id: number) => void }) {
  if (state.status === "loading") return <CardV2 title="Before the open" testId="journal-today"><LoadingRowsV2 rows={3} /></CardV2>;
  if (state.status === "error") return <CardV2 title="Before the open" testId="journal-today"><ErrorState message={state.error} onRetry={onRetry} variant="inline" /></CardV2>;
  const d = state.data;
  return (
    <div className="space-y-space-4">
      {d.reminders.length > 0 && (
        <CardV2 title="Your plan for this moment" testId="plan-reminders">
          <ul className="space-y-space-2">
            {d.reminders.map((r) => (
              <li key={r.id} className="text-t14 text-fg" data-reminder={r.id}>
                <span className="block text-t12 text-fg-muted">{r.because}</span>
                <span className="font-semibold">If</span> {r.if_text}, <span className="font-semibold">then</span> {r.then_text}
              </li>
            ))}
          </ul>
        </CardV2>
      )}
      <CardV2 title="Before the open" testId="journal-today" tour="journal-today">
        {d.plans.length > 0 ? <IfThenListV2 plans={d.plans} />
          : <p className="text-t14 text-fg-muted">Your if-then plans show here. Add one on the Plans tab.</p>}
        {d.goals.length > 0 && <div className="mt-space-4"><GoalsListV2 goals={d.goals} /></div>}
      </CardV2>
      <LastGoodV2 fact={d.last_good} afterLoss={d.after_loss} onOpen={onOpen} />
    </div>
  );
}

const DELIVERY_DEFAULT: JournalDelivery = { telegram: false, email: false, daily: false, weekly: false, monthly: false, checkin_prompts: false,
  daily_hour: 17, weekly_day: "SAT" };
const BOX = "h-5 w-5 shrink-0 accent-brand";
const ROW = "flex min-h-11 items-center gap-space-3 text-t14 text-fg";

/** P2-8 J6: where his journal goes - his own Telegram chat and / or e-mail; which reviews; when. All OFF by default. */
export function DeliveryV2({ api }: { api: Pick<JournalApi, "delivery" | "saveDelivery"> }) {
  const toast = useToast();
  const [value, setValue] = useState<JournalDelivery | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    api.delivery().then((v) => { if (alive) setValue({ ...DELIVERY_DEFAULT, ...v }); }).catch(() => { if (alive) setValue(DELIVERY_DEFAULT); });
    return () => { alive = false; };
  }, [api]);
  if (!value) return <CardV2 title="Send my journal" testId="journal-delivery"><LoadingRowsV2 rows={3} /></CardV2>;
  const set = (patch: Partial<JournalDelivery>) => setValue({ ...value, ...patch });
  const box = (key: keyof JournalDelivery, label: string) => (
    <label className={ROW}>
      <input type="checkbox" className={BOX} checked={Boolean(value[key])} onChange={(e) => set({ [key]: e.target.checked } as Partial<JournalDelivery>)} data-delivery={key} />
      {label}
    </label>
  );
  return (
    <CardV2 title="Send my journal" testId="journal-delivery" tour="journal-delivery">
      <div className="grid gap-x-space-6 md:grid-cols-2">
        <div>
          <span className={LABEL}>Where</span>
          {box("telegram", "My Telegram chat")}
          {box("email", "My e-mail")}
        </div>
        <div>
          <span className={LABEL}>What</span>
          {box("daily", "Daily card")}
          {box("weekly", "Weekly review")}
          {box("monthly", "Monthly page")}
          {box("checkin_prompts", "Check-in reminders")}
        </div>
        <div className="min-w-0">
          <span aria-hidden className={LABEL}>Daily card after</span>
          <Combobox label="Daily card after" value={String(value.daily_hour)} onChange={(v) => set({ daily_hour: Number(v) })} dataAttrs={{ "data-delivery-hour": "" }}
            options={[16, 17, 18, 19, 20, 21, 22].map((h) => ({ value: String(h), label: `${h}:00 IST` }))} />
        </div>
        <div className="min-w-0">
          <span aria-hidden className={LABEL}>Weekly review on</span>
          <Combobox label="Weekly review on" value={value.weekly_day} onChange={(v) => set({ weekly_day: v as "SAT" | "SUN" })} dataAttrs={{ "data-delivery-day": "" }}
            options={[{ value: "SAT", label: "Saturday" }, { value: "SUN", label: "Sunday" }]} />
        </div>
      </div>
      <p className="mt-space-3 text-t12 text-fg-muted">Counts and results only. Your feelings and notes stay here.</p>
      <div className="mt-space-3 flex justify-end">
        <button type="button" className={BUTTON_PRIMARY} disabled={busy} data-delivery-save
          onClick={async () => { setBusy(true); try { await api.saveDelivery(value); toast.success("Saved."); } catch (e) { toast.error(errorSentence(e)); } finally { setBusy(false); } }}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </CardV2>
  );
}
