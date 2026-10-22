import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/errors";
import { BYOK_CHANGED_EVENT } from "../aiKeys/detect";
import { AI_CONNECTION_CHANGED_EVENT } from "../aiKeys/connect";
import { CARD } from "../dashboard/CardV2";
import type { ByokAuditScope, ByokChannels, ByokPrefsInput, ByokStatus } from "../types";

/**
 * Settings redesign B (owner question from Part A, provisional default): with one key for every AI feature
 * (`ai_one_key`), Settings → AI shows only the Connect AI panel, so the switches of Vision's background reviews (R2:
 * `prefs.enabled` and the audit scopes, which control only those reviews) live here, under Settings → Advanced. The key
 * and the spending limits stay in the Connect AI panel. Nothing renders while Vision is not enabled (`vision_byok` off:
 * the route answers 404).
 */

const AUDIT: { key: keyof ByokAuditScope; label: string }[] = [
  { key: "proposals", label: "Trade proposals" },
  { key: "scan_alerts", label: "Every scan alert" },
  { key: "daily_review", label: "Daily review" },
];
const CHANNELS: { key: keyof ByokChannels; label: string }[] = [
  { key: "in_app", label: "In the app" },
  { key: "telegram", label: "Telegram" },
];
const BOX = "h-5 w-5 shrink-0 accent-brand";
const ROW = "flex min-h-11 items-center gap-space-3 text-t14 text-fg";

export default function VisionReviewSwitchesV2() {
  const [status, setStatus] = useState<ByokStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    const read = () => api.byokStatus().then((s) => { if (live) setStatus(s); }, (e) => {
      if (live && !(e instanceof ApiError && e.status === 404)) setNote("Could not load the Vision settings. Try again later.");
    });
    void read();
    window.addEventListener(BYOK_CHANGED_EVENT, read);
    window.addEventListener(AI_CONNECTION_CHANGED_EVENT, read);
    return () => { live = false; window.removeEventListener(BYOK_CHANGED_EVENT, read); window.removeEventListener(AI_CONNECTION_CHANGED_EVENT, read); };
  }, []);

  async function save(body: ByokPrefsInput) {
    setBusy(true); setNote(null);
    try { setStatus(await api.byokSavePrefs(body)); setNote("Saved."); }
    catch (e) { setNote(e instanceof ApiError && e.detail ? e.detail : "Could not save. Try again."); }
    finally { setBusy(false); }
  }

  if (!status) return note ? <p className="text-t13 text-fg-muted">{note}</p> : null;
  const v3Off = status.scope != null && !status.scope.v3_enabled;
  return (
    <div className={`${CARD} space-y-space-3 p-space-4 md:p-space-5`} data-vision-switches>
      <p className="text-t14 text-fg-muted">Vision looks at charts in the background with your AI key and writes a short comment. It never places, blocks or changes an order.</p>
      <label className={ROW}>
        <input type="checkbox" className={BOX} checked={status.enabled} disabled={busy} onChange={(e) => void save({ enabled: e.target.checked })} />
        Background chart reviews on
      </label>
      <fieldset className="space-y-space-1" disabled={busy || !status.enabled}>
        <legend className="text-t13 font-medium text-fg-muted">Which charts</legend>
        {AUDIT.map(({ key, label }) => {
          const off = key !== "proposals" && v3Off;
          return (
            <label key={key} className={`${ROW} ${off ? "text-fg-muted" : ""}`}>
              <input type="checkbox" className={BOX} checked={status.audit[key]} disabled={off} onChange={(e) => void save({ audit: { [key]: e.target.checked } })} />
              {label}{off ? " (not available yet)" : ""}
            </label>
          );
        })}
      </fieldset>
      <fieldset className="space-y-space-1" disabled={busy || !status.enabled}>
        <legend className="text-t13 font-medium text-fg-muted">Where the comments go</legend>
        {CHANNELS.map(({ key, label }) => (
          <label key={key} className={ROW}>
            <input type="checkbox" className={BOX} checked={status.channels[key]} onChange={(e) => void save({ channels: { [key]: e.target.checked } })} />
            {label}
          </label>
        ))}
      </fieldset>
      {note && <p className="text-t13 text-fg-muted" role="status">{note}</p>}
    </div>
  );
}
