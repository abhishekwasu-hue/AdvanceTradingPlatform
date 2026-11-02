import { useEffect, useState } from "react";
import { cx } from "../../components/primitives/cx";
import { Details } from "../../ui/Details";
import { formatNumber } from "../../ui/format";
import type { Annotations } from "./annotations";
import { addArea, areaText, areaToWatch, blockedReason, KIND_ROWS, MAX_WATCHED, numberOf, watchApi, type WatchApi, type WatchPrefs, type WatchSettings } from "./watchCards";

/**
 * M76 MP-11 (owner order M76 D-1; flag `copilot_workspace`): his watch cards, set up in place in the workspace - the six
 * types (each OFF, each number his own), the areas of this chart he chose to watch, and Telegram to his own chat. The
 * cards themselves arrive in Notifications (and Telegram) with "Open here". Information only: no order route here;
 * nothing placed, held back or changed. Rules: `watchCards.ts` (tested in watchCards.test.tsx).
 */
const FIELD = "h-11 w-24 rounded-control border border-border bg-surface-2 px-space-2 text-t14 text-fg font-tabular focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9";
const BTN = "inline-flex min-h-11 items-center justify-center rounded-control px-space-3 text-t13 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50 md:min-h-9";
const fmt = (n: number) => formatNumber(n, 2);

export function WatchCardsV2({ instrument, tf, annotations, api = watchApi }: {
  instrument: string | null; tf: string | null; annotations: Annotations | null; api?: WatchApi;
}) {
  const [settings, setSettings] = useState<WatchSettings | null>(null);
  const [draft, setDraft] = useState<WatchPrefs | null>(null);
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [note, setNote] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let live = true;
    api.read().then((s) => { if (live) { setSettings(s); setDraft(s.prefs); } }, () => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [api]);

  if (failed) return <p className="text-t12 text-fg-muted" data-watch-cards="failed">Watch cards could not be read just now.</p>;
  if (!settings || !draft) return null;
  const squareOff = settings.square_off_time;
  const chartAreas = instrument && tf && annotations ? annotations.areas.map((a) => areaToWatch(a, instrument, tf)).filter((a) => a !== null) : [];

  async function save(next: WatchPrefs) {
    try {
      const s = await api.save(next);
      setSettings(s); setDraft(s.prefs); setNote("Saved.");
    } catch (e) {
      setNote(`Not saved: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  const on = KIND_ROWS.filter((r) => draft[r.kind]).length;

  return (
    <div data-watch-cards={on}>
      <Details id="copilot.watch-cards" label={on ? `Watch cards (${on} on)` : "Watch cards"}>
        <div className="space-y-space-3 text-t13 text-fg">
          <ul className="divide-y divide-subtle" aria-label="Watch card types">
            {KIND_ROWS.map((r) => {
              const blocked = blockedReason(draft, r, squareOff);
              return (
                <li key={r.kind} className="flex flex-wrap items-center gap-x-space-3 gap-y-space-1 py-space-1" data-watch-kind={r.kind}>
                  <label className="flex min-h-11 items-center gap-space-2 md:min-h-9">
                    <input type="checkbox" checked={draft[r.kind]} disabled={!draft[r.kind] && !!blocked}
                      onChange={(e) => setDraft({ ...draft, [r.kind]: e.target.checked })} data-watch-toggle={r.kind} />
                    {r.label}
                  </label>
                  {r.field && (
                    <span className="flex items-center gap-space-1">
                      <input aria-label={`${r.label} (${r.unit})`} inputMode="decimal" className={FIELD} placeholder="Not set" data-watch-number={r.kind}
                        value={texts[r.kind] ?? (draft[r.field] != null ? String(draft[r.field]) : "")}
                        onChange={(e) => {
                          const v = numberOf(r, e.target.value);
                          setTexts({ ...texts, [r.kind]: e.target.value });
                          setDraft({ ...draft, [r.field as string]: v, ...(v == null ? { [r.kind]: false } : {}) });
                        }} />
                      <span className="text-fg-muted">{r.unit}{r.kind === "cut_off_reminder" && squareOff ? ` before ${squareOff}` : ""}</span>
                    </span>
                  )}
                  {blocked && !draft[r.kind] && <span className="text-t12 text-fg-muted" data-watch-blocked={r.kind}>{blocked}</span>}
                </li>
              );
            })}
          </ul>

          <div className="space-y-space-1" data-watch-areas>
            <p className="text-t12 font-semibold text-fg-muted">Your watched areas ({draft.areas.length} of {MAX_WATCHED})</p>
            {draft.areas.length > 0 ? (
              <ul className="divide-y divide-subtle">
                {draft.areas.map((a) => (
                  <li key={a.id} className={cx("flex items-center justify-between gap-space-2 border-l-2 py-space-1 pl-space-2", a.side === "buyer" ? "border-up" : "border-down")} data-watched={a.id}>
                    <span className="min-w-0 break-words font-tabular">{areaText(a, fmt)}</span>
                    <button type="button" className={cx(BTN, "text-fg-muted hover:text-fg")} data-watch-remove={a.id}
                      onClick={() => setDraft({ ...draft, areas: draft.areas.filter((x) => x.id !== a.id) })}>Remove</button>
                  </li>
                ))}
              </ul>
            ) : <p className="text-t12 text-fg-muted">None yet.</p>}
            {chartAreas.length > 0 && (
              <ul className="space-y-space-1" aria-label="Areas on this chart">
                {chartAreas.map((a) => {
                  const watched = draft.areas.some((x) => x.id === a.id);
                  return (
                    <li key={a.id} className="flex items-center justify-between gap-space-2" data-chart-area={a.id}>
                      <span className="min-w-0 break-words font-tabular text-fg-muted">{areaText(a, fmt)}</span>
                      <button type="button" className={cx(BTN, "border border-border text-fg hover:bg-surface-2")} disabled={watched}
                        data-watch-add={a.id} onClick={() => {
                          const next = addArea(draft.areas, a);
                          if (next) setDraft({ ...draft, areas: next });
                          else setNote(`At most ${MAX_WATCHED} watched areas.`);
                        }}>{watched ? "Watching" : "Watch"}</button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          <label className="flex min-h-11 items-center gap-space-2 md:min-h-9">
            <input type="checkbox" checked={draft.telegram} onChange={(e) => setDraft({ ...draft, telegram: e.target.checked })} data-watch-telegram />
            Also to my Telegram
            {draft.telegram && !settings.telegram.reaches_you && <span className="text-t12 text-fg-muted" data-watch-telegram-off>(not linked yet)</span>}
          </label>
          <div className="flex flex-wrap items-center gap-space-2">
            <button type="button" className={cx(BTN, "bg-brand text-on-brand")} onClick={() => void save(draft)} data-watch-save>Save</button>
            {note && <span role="status" className="text-t12 text-fg" data-watch-note>{note}</span>}
          </div>
          <p className="text-t12 text-fg-muted">Cards arrive in Notifications with Open here. A card never places, holds back or changes an order.</p>
        </div>
      </Details>
    </div>
  );
}

export default WatchCardsV2;
