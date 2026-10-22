import { BellRing, Smartphone } from "lucide-react";
import { useState } from "react";
import { api } from "../api/client";
import type { AlertChannel, AlertDelivery, NotificationSeverity } from "../types";
import { PUSH_CATEGORIES, currentPushEnv, pushReadiness, selectedCategories, vapidKeyBytes } from "./push";

/**
 * PW3: browser push on every platform, inside Settings -> Alert delivery.
 * - The browser is asked for permission only after the user has read what they will get and chosen "Continue".
 * - iPhone / iPad: push works only in the app added to the Home Screen; the section says so instead of failing.
 * - Categories decide which notifications reach the devices (EMERGENCY always does); a click on a notification
 *   opens the exact screen (the backend puts the in-app path in every push).
 * - "Send test" goes through the stored channel now; the last push deliveries are listed below it.
 */
export default function WebPushSection({ channel, deliveries, busy, run, test }: {
  channel: AlertChannel | undefined;
  deliveries: AlertDelivery[];
  busy: boolean;
  run: (label: string, fn: () => Promise<unknown>) => void;
  test: () => void;
}) {
  const [explaining, setExplaining] = useState(false);
  const readiness = pushReadiness(currentPushEnv());
  const devices = (channel?.config.devices as { label: string; endpoint: string }[] | undefined) ?? [];
  const chosen = selectedCategories(channel?.config.categories);
  const floor = (channel?.min_severity ?? "WARNING") as NotificationSeverity;

  async function enableHere() {
    const { public_key } = await api.pushPublicKey();
    const registration = await navigator.serviceWorker.register("/sw.js");
    const permission = await Notification.requestPermission();
    if (permission !== "granted") throw new Error("Notifications were not allowed in the browser - nothing was registered");
    const sub = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: vapidKeyBytes(public_key) });
    const json = sub.toJSON();
    const label = `${navigator.platform || "device"} · ${/Chrome|Firefox|Safari|Edg/.exec(navigator.userAgent)?.[0] ?? "browser"}`;
    await api.upsertAlertChannel("push", { enabled: true, min_severity: floor,
      config: { subscription: { endpoint: json.endpoint, p256dh: json.keys?.p256dh, auth: json.keys?.auth, label } } });
  }

  function toggleCategory(id: string) {
    const next = new Set(chosen);
    if (next.has(id)) next.delete(id); else next.add(id);
    run("Push preferences saved.", () => api.upsertAlertChannel("push", { enabled: channel?.enabled ?? true, min_severity: floor,
      config: { categories: PUSH_CATEGORIES.map((c) => c.id).filter((c) => next.has(c)) } }));
  }

  const pushDeliveries = deliveries.filter((d) => d.channel_type === "PUSH").slice(0, 5);

  return (
    <div className="rounded-lg border border-border bg-surface-2/40 p-3 space-y-2" data-testid="push-section">
      <div className="flex items-center gap-2 text-sm font-bold text-fg"><Smartphone size={14} /> Push notifications</div>
      <p className="text-xs text-fg-muted">
        Alerts as system notifications on this phone or computer, even with the app closed. Messages are encrypted to this
        device; the push service in between cannot read them. Tapping one opens the exact screen - the proposal, the position
        or the alert. Add each device you use.
      </p>

      {readiness === "ios-install-first" && (
        <p className="rounded border border-warn/40 bg-warn/10 p-2 text-xs text-fg" data-testid="push-ios">
          On iPhone and iPad, notifications work only in the installed app: in Safari tap <b>Share</b>, then <b>Add to Home
          Screen</b>, open AiAlgoDesk from the Home Screen and come back here. (iOS 16.4 or later.)
        </p>
      )}
      {readiness === "unsupported" && <p className="text-xs text-fg-muted">This browser cannot receive push notifications. Telegram or email still work.</p>}
      {readiness === "denied" && (
        <p className="rounded border border-down/40 bg-down/10 p-2 text-xs text-fg" data-testid="push-denied">
          Notifications are blocked for this site. Allow them in the browser&apos;s site settings (the lock icon next to the
          address), then press Enable again.
        </p>
      )}

      {devices.map((d) => (
        <div key={d.endpoint} className="flex items-center justify-between text-xs">
          <span className="text-fg">{d.label}</span>
          <button disabled={busy} onClick={() => run("Device removed.", () => devices.length > 1
            ? api.upsertAlertChannel("push", { enabled: true, min_severity: floor, config: { remove_endpoint: d.endpoint } })
            : api.deleteAlertChannel("push"))} className="text-down hover:underline">remove</button>
        </div>
      ))}

      {explaining ? (
        <div className="space-y-2 rounded border border-brand/40 bg-brand/5 p-2 text-xs text-fg" data-testid="push-explain">
          <p className="flex items-center gap-1 font-semibold"><BellRing size={13} /> Before your browser asks</p>
          <p>You will get the kinds of alert ticked below at <b>{floor}</b> severity or higher, as notifications on this device.
             Emergencies always come through. Nothing is sent to anyone else, and you can turn it off here or in the browser at any time.</p>
          <div className="flex gap-2">
            <button disabled={busy} onClick={() => { setExplaining(false); run("Push enabled on this device.", enableHere); }}
                    className="rounded bg-brand px-3 py-1 font-semibold text-on-brand hover:bg-brand-strong disabled:opacity-50">Continue</button>
            <button onClick={() => setExplaining(false)} className="rounded border border-border px-3 py-1 hover:bg-surface-2">Not now</button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button disabled={busy || readiness === "unsupported" || readiness === "ios-install-first"} onClick={() => setExplaining(true)}
                  className="rounded bg-brand hover:bg-brand-strong text-on-brand font-semibold px-3 py-1 text-xs disabled:opacity-50">Enable on this device</button>
          <button disabled={busy || !channel} onClick={test} className="rounded border border-border hover:bg-surface-2 text-fg px-3 py-1 text-xs disabled:opacity-50">Send test</button>
          {channel && <button disabled={busy} onClick={() => run("Push channel removed.", () => api.deleteAlertChannel("push"))} className="text-xs text-down hover:underline">Remove all</button>}
        </div>
      )}

      <fieldset className="space-y-1" disabled={busy || !channel}>
        <legend className="text-xs font-semibold text-fg-muted">Send these to my devices{channel ? "" : " (enable a device first)"}</legend>
        {PUSH_CATEGORIES.map((c) => (
          <label key={c.id} className="flex items-center gap-2 text-xs text-fg">
            <input type="checkbox" checked={chosen.has(c.id)} onChange={() => toggleCategory(c.id)} /> {c.label}
          </label>
        ))}
      </fieldset>

      {pushDeliveries.length > 0 && (
        <div className="text-xs text-fg-muted" data-testid="push-log">
          <div className="font-semibold">Last push deliveries</div>
          {pushDeliveries.map((d) => (
            <div key={d.id} className="flex justify-between gap-2">
              <span className="truncate">{d.title}</span>
              <span className={d.status === "SENT" ? "text-up" : d.status === "FAILED" ? "text-down" : ""}>{d.status}{d.last_error ? ` · ${d.last_error}` : ""}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
