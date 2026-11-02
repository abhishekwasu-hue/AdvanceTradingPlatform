import { useEffect, useState } from "react";
import { cx } from "../../components/primitives/cx";
import { Details } from "../../ui/Details";
import { formatDateIST } from "../../ui/format";
import { addOwn, eventChips, eventsApi, eventsNote, MAX_OWN_EVENTS, ownEventOf, type EventsApi, type OwnEvent, type WorkspaceEvents } from "./events";

/**
 * M76 MP-7 (owner order M76 S-3; flag `copilot_workspace`): the events strip on the workspace's chart - the listed
 * expiries (weekly / monthly), the events calendar's entries and his own events, up to the next monthly expiry - and his
 * own event list (add / remove, in his prefs). The check card's event line reads the same list. Information only: no
 * order route here; nothing placed, held back or changed. Rules and words: `events.ts` (tested in events.test.tsx).
 */
const FIELD = "h-11 w-full min-w-0 rounded-control border border-border bg-surface-2 px-space-2 text-t14 text-fg focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9";
const BTN = "inline-flex min-h-11 items-center justify-center rounded-control px-space-3 text-t13 font-semibold focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50 md:min-h-9";
const TONE: Record<string, string> = {
  expiry: "border-brand/40 text-fg", market: "border-warn/50 text-fg", company: "border-border text-fg", own: "border-border text-fg-muted",
};

export function EventsV2({ symbol, api = eventsApi }: { symbol: string; api?: EventsApi }) {
  const [view, setView] = useState<WorkspaceEvents | null>(null);
  const [failed, setFailed] = useState(false);
  const [own, setOwn] = useState<{ list: OwnEvent[]; version: number } | null>(null);
  const [date, setDate] = useState("");
  const [name, setName] = useState("");
  const [forThis, setForThis] = useState(true);
  const [note, setNote] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let live = true;
    setView(null); setFailed(false);
    api.read(symbol).then((v) => { if (live) setView(v); }, () => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [symbol, api, tick]);
  useEffect(() => {
    let live = true;
    api.loadOwn().then((o) => { if (live) setOwn(o); }, () => undefined);   // intentional: his list just does not show; the strip still does
    return () => { live = false; };
  }, [api]);

  const chips = eventChips(view);
  const line = failed ? "The events could not be read just now." : eventsNote(view);
  async function save(next: OwnEvent[]) {
    if (!own) return;
    try {
      setOwn(await api.saveOwn(next, own.version));
      setNote(null);
      setTick((n) => n + 1);
    } catch (e) {
      setNote(`Not saved: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  function add() {
    const e = ownEventOf(date, name, forThis ? symbol : null);
    if (typeof e === "string") { setNote(e); return; }
    const next = own ? addOwn(own.list, e) : null;
    if (!next) { setNote(`Your list holds at most ${MAX_OWN_EVENTS} events.`); return; }
    setDate(""); setName("");
    void save(next);
  }

  return (
    <div className="space-y-space-2" data-workspace-events={view ? chips.length : failed ? "failed" : "loading"}>
      {chips.length > 0 && (
        <ul aria-label="Events on this chart" className="flex flex-wrap gap-space-1">
          {chips.map((c) => (
            <li key={c.key} data-event-chip={c.tone} className={cx("rounded-full border px-space-2 py-0.5 text-t12 font-tabular", TONE[c.tone])}>{c.text}</li>
          ))}
        </ul>
      )}
      {line && <p className="text-t12 text-fg-muted" data-events-note>{line}</p>}
      <Details id="copilot.events" label="Your events">
        <div className="space-y-space-2" data-own-events>
          {own && own.list.length > 0 ? (
            <ul className="divide-y divide-subtle text-t13">
              {own.list.map((e, i) => (
                <li key={`${e.date}-${e.name}-${i}`} className="flex items-center justify-between gap-space-2 py-space-1" data-own-event>
                  <span className="min-w-0"><span className="font-tabular">{formatDateIST(e.date, { weekday: true })}</span> · {e.name}
                    <span className="text-fg-muted"> · {e.underlying ?? "every symbol"}</span></span>
                  <button type="button" className={cx(BTN, "text-fg-muted hover:text-fg")} data-own-remove={i}
                    onClick={() => void save(own.list.filter((_x, j) => j !== i))}>Remove</button>
                </li>
              ))}
            </ul>
          ) : <p className="text-t12 text-fg-muted">Your list is empty. Events you add show here and in the check card.</p>}
          <div className="grid grid-cols-1 gap-space-2 md:grid-cols-[auto_minmax(0,1fr)_auto_auto] md:items-end">
            <label className="flex flex-col gap-space-1"><span className="text-t12 text-fg-muted">Date</span>
              <input type="date" aria-label="Date" className={FIELD} value={date} onChange={(e) => setDate(e.target.value)} data-own-date /></label>
            <label className="flex min-w-0 flex-col gap-space-1"><span className="text-t12 text-fg-muted">Event</span>
              <input aria-label="Event" className={FIELD} value={name} maxLength={40} placeholder="Not set" onChange={(e) => setName(e.target.value)} data-own-name /></label>
            <label className="flex min-h-11 items-center gap-space-2 text-t13 text-fg md:min-h-9">
              <input type="checkbox" checked={forThis} onChange={(e) => setForThis(e.target.checked)} data-own-scope />Only {symbol}</label>
            <button type="button" className={cx(BTN, "border border-border text-fg hover:bg-surface-2")} disabled={!own} onClick={add} data-own-add>Add</button>
          </div>
          {note && <p role="status" className="text-t12 text-fg" data-own-note>{note}</p>}
          <p className="text-t12 text-fg-muted">Information only. An event never holds back an order or an exit.</p>
        </div>
      </Details>
    </div>
  );
}

export default EventsV2;
