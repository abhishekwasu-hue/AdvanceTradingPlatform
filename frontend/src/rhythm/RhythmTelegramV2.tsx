import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/errors";
import { DEFAULT_PREFS, TELEGRAM_CHOICES } from "./model";
import type { RhythmPrefs, RhythmSettings } from "./types";
import { Skeleton } from "../components/primitives";
import { Details } from "../ui/Details";

/**
 * MO4 (Settings → Alerts, behind `ui_v2` + `daily_rhythm`): "Daily briefs on Telegram" - OFF until the user turns it on,
 * then the four digests to choose from (the mid-session pulse optional, off by default). The briefs go only to the
 * user's own Telegram chat; until Telegram reaches it, the switch stays off with one plain next step (the Telegram row
 * just above). The briefs on the Dashboard do not depend on this.
 */

const BOX = "h-5 w-5 shrink-0 accent-brand";
const ROW = "flex min-h-11 items-center gap-space-3 text-t14 text-fg";

export function telegramNote(s: RhythmSettings): string | null {
  if (!s.telegram.connected) return "Connect Telegram above first. The briefs go to your own chat.";
  if (!s.telegram.reaches_you) return "Telegram here is connected to another member's chat. Connect your own to get the briefs.";
  return null;
}

export default function RhythmTelegramV2() {
  const [settings, setSettings] = useState<RhythmSettings | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [hidden, setHidden] = useState(false);

  useEffect(() => {
    let live = true;
    api.rhythmSettings().then((s) => { if (live) setSettings(s); }, (e: unknown) => {
      if (!live) return;
      if (e instanceof ApiError && e.status === 404) setHidden(true);
      else setNote("Could not load your brief settings. Try again later.");
    });
    return () => { live = false; };
  }, []);

  async function save(next: RhythmPrefs) {
    setBusy(true); setNote(null);
    try { setSettings(await api.saveRhythmSettings(next)); setNote("Saved."); }
    catch { setNote("Could not save. Try again."); }
    finally { setBusy(false); }
  }

  if (hidden) return null;
  if (!settings) return note ? <p className="text-t13 text-fg-muted">{note}</p> : <Skeleton className="h-20" label="Loading" />;
  const prefs = { ...DEFAULT_PREFS, ...settings.prefs };
  const blocked = telegramNote(settings);
  const on = prefs.telegram && !blocked;
  return (
    <div className="space-y-space-3" data-rhythm-telegram>
      <Details id="rhythm.telegram" className="mt-0">
        <p className="text-t14 text-fg-muted">
          A short brief before the open, a closing wrap and your week, from your own lists and trades. It describes the market and your
          record; it never tells you what to trade.
        </p>
      </Details>
      <label className={ROW}>
        <input type="checkbox" className={BOX} checked={on} disabled={busy || blocked !== null}
          onChange={(e) => void save({ ...prefs, telegram: e.target.checked })} />
        Send my daily briefs to Telegram
      </label>
      {blocked && <p className="text-t13 text-fg-muted">{blocked}</p>}
      {on && (
        <fieldset className="space-y-space-1" disabled={busy}>
          <legend className="text-t13 font-medium text-fg-muted">Which briefs</legend>
          {TELEGRAM_CHOICES.map(({ key, label, line }) => (
            <label key={key} className={ROW}>
              <input type="checkbox" className={BOX} checked={prefs[key]} onChange={(e) => void save({ ...prefs, [key]: e.target.checked })} />
              <span className="flex min-w-0 flex-col">
                <span>{label}</span>
                <span className="text-t13 text-fg-muted">{line}</span>
              </span>
            </label>
          ))}
        </fieldset>
      )}
      {note && <p className="text-t13 text-fg-muted" role="status">{note}</p>}
    </div>
  );
}
