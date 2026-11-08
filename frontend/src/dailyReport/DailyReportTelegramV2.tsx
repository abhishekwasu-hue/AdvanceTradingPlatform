import { useEffect, useState } from "react";
import { ApiError } from "../api/errors";
import { Skeleton } from "../components/primitives";
import { Details } from "../ui/Details";
import { reportApi } from "./api";
import type { ReportTelegram } from "./types";

/**
 * M89 TG2 (Settings › Alerts, behind `shared_daily_report`): "Daily market report on Telegram" - ON by default for a user
 * whose Telegram is connected; he turns it off here (or with Stop under the message). Hidden while the flag is off.
 */
const ROW = "flex min-h-11 items-center gap-space-3 text-t14 text-fg";

export function telegramReportNote(s: ReportTelegram): string | null {
  return s.connected ? null : "Connect Telegram above first. The report goes to your own chat.";
}

export default function DailyReportTelegramV2() {
  const [state, setState] = useState<ReportTelegram | null>(null);
  const [hidden, setHidden] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let live = true;
    reportApi.telegram().then((s) => { if (live) setState(s); }, (e: unknown) => {
      if (!live) return;
      if (e instanceof ApiError && e.status === 404) setHidden(true);
      else setNote("Could not load this setting. Try again later.");
    });
    return () => { live = false; };
  }, []);
  async function save(on: boolean) {
    setBusy(true); setNote(null);
    try { setState(await reportApi.saveTelegram(on)); setNote("Saved."); }
    catch { setNote("Could not save. Try again."); }
    finally { setBusy(false); }
  }
  if (hidden) return null;
  if (!state) return note ? <p className="text-t13 text-fg-muted">{note}</p> : <Skeleton className="h-12" label="Loading" />;
  const blocked = telegramReportNote(state);
  return (
    <div className="space-y-space-2" data-report-telegram-setting>
      <label className={ROW}>
        <input type="checkbox" className="h-5 w-5 shrink-0 accent-brand" checked={state.telegram && !blocked} disabled={busy || blocked !== null}
          onChange={(e) => void save(e.target.checked)} />
        Daily market report on Telegram
      </label>
      <Details id="daily-report.telegram" className="mt-0">
        <p className="text-t14 text-fg-muted">After each market close: a short summary and the full report as a PDF, in your own chat.</p>
      </Details>
      {blocked && <p className="text-t13 text-fg-muted">{blocked}</p>}
      {note && <p className="text-t13 text-fg-muted" role="status">{note}</p>}
    </div>
  );
}
