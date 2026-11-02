import { useEffect, useState } from "react";
import { ApiError } from "../../api/errors";
import { Button, Skeleton } from "../../components/primitives";
import { Details } from "../../ui/Details";
import { EMPTY_PREFS, WATCH_CHOICES, canTurnOn, normalise, parseNumber, watchApi, type WatchPrefs, type WatchSettings } from "./model";

/**
 * M72 PR-7 (CP-7, Settings › Alerts, behind `ui_v2` + `copilot_watch`): "Copilot watch" - each alert type is off until the
 * user turns it on with HIS own number (the box stays disabled while the number is Not set). Cards land in Notifications
 * with "Ask why", and on his own Telegram when he chooses. Information only: nothing is placed, closed or blocked.
 */

const BOX = "h-5 w-5 shrink-0 accent-brand";
const ROW = "flex min-h-11 items-start gap-space-3 text-t14 text-fg";
const FIELD = "h-11 w-24 min-w-0 rounded-control border border-border bg-surface-2 px-space-3 text-t14 text-fg placeholder:text-fg-muted focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand md:h-9";

function numText(v: number | null): string {
  return v === null ? "" : String(v);
}

export function CopilotWatchForm({ settings, busy, onSave }: { settings: WatchSettings; busy: boolean; onSave: (p: WatchPrefs) => void }) {
  const [draft, setDraft] = useState<WatchPrefs>({ ...EMPTY_PREFS, ...settings.prefs });
  const [texts, setTexts] = useState<Record<string, string>>(() =>
    Object.fromEntries(WATCH_CHOICES.flatMap((c) => c.numbers.map((n) => [n.key, numText(settings.prefs[n.key])]))));
  const telegramReady = settings.telegram.connected && settings.telegram.reaches_you;
  return (
    <form className="space-y-space-3" data-copilot-watch onSubmit={(e) => { e.preventDefault(); onSave(normalise(draft)); }}>
      <Details id="copilot.watch" className="mt-0">
        <p className="text-t14 text-fg-muted">
          Short cards about your own positions, short strikes and watchlists, with Ask why. Each type uses your own number; ATP sets none.
          They describe what changed; they never tell you what to trade, and nothing is placed or closed by ATP.
        </p>
      </Details>
      {WATCH_CHOICES.map((c) => {
        const ready = canTurnOn(draft, c);
        return (
          <div key={c.kind} className="space-y-space-1 border-t border-border pt-space-2" data-watch-kind={c.kind}>
            <label className={ROW}>
              <input type="checkbox" className={`${BOX} mt-space-1`} checked={draft[c.kind] && ready} disabled={busy || !ready}
                onChange={(e) => setDraft({ ...draft, [c.kind]: e.target.checked })} />
              <span className="flex min-w-0 flex-col">
                <span>{c.label}</span>
                <span className="text-t13 text-fg-muted">{c.line}</span>
                {!ready && <span className="text-t13 text-fg-muted">Set your number first.</span>}
              </span>
            </label>
            {c.numbers.length > 0 && (
              <div className="flex flex-wrap gap-space-3 pl-space-8">
                {c.numbers.map((n) => {
                  const id = `watch-${n.key}`;
                  const disabled = n.key === "delta_jump" && !settings.risk_free_rate_set;
                  return (
                    <span key={n.key} className="flex flex-col gap-space-1">
                      <label htmlFor={id} className="text-t13 text-fg-muted">{n.label}{n.unit ? ` (${n.unit})` : ""}</label>
                      <input id={id} inputMode="decimal" className={FIELD} placeholder="Not set" value={texts[n.key] ?? ""} disabled={busy || disabled}
                        onChange={(e) => {
                          setTexts({ ...texts, [n.key]: e.target.value });
                          setDraft({ ...draft, [n.key]: parseNumber(e.target.value, n.max) });
                        }} />
                      {disabled && <span className="text-t13 text-fg-muted">Needs your interest rate for the Greeks (Trade settings › Options).</span>}
                    </span>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}
      <label className={`${ROW} border-t border-border pt-space-2`}>
        <input type="checkbox" className={`${BOX} mt-space-1`} checked={draft.telegram && telegramReady} disabled={busy || !telegramReady}
          onChange={(e) => setDraft({ ...draft, telegram: e.target.checked })} />
        <span className="flex min-w-0 flex-col">
          <span>Also send the cards to my Telegram</span>
          <span className="text-t13 text-fg-muted">{telegramReady ? "Your own chat only. Always in Notifications." : "Connect your own Telegram above first."}</span>
        </span>
      </label>
      <Button type="submit" variant="primary" size="v2" disabled={busy}>Save</Button>
    </form>
  );
}

export default function CopilotWatchV2() {
  const [settings, setSettings] = useState<WatchSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    let live = true;
    watchApi.settings().then((s) => { if (live) setSettings(s); }, (e: unknown) => {
      if (!live) return;
      if (e instanceof ApiError && e.status === 404) setHidden(true);
      else setNote("Could not load your watch settings. Try again later.");
    });
    return () => { live = false; };
  }, []);

  async function save(next: WatchPrefs) {
    setBusy(true); setNote(null);
    try { setSettings(await watchApi.save(next)); setNote("Saved."); }
    catch { setNote("Could not save. Check your numbers and try again."); }
    finally { setBusy(false); }
  }

  if (hidden) return null;
  if (!settings) return note ? <p className="text-t13 text-fg-muted">{note}</p> : <Skeleton className="h-20" label="Loading" />;
  return (
    <div className="space-y-space-2">
      <CopilotWatchForm key={settings.version} settings={settings} busy={busy} onSave={(p) => void save(p)} />
      {note && <p className="text-t13 text-fg-muted" role="status">{note}</p>}
    </div>
  );
}
