import { BellRing, Smartphone } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { ApiError } from "../api/errors";
import { appVersions, versionLine } from "../native/version";
import { ActionV2, Note } from "../setup/StepsV2";
import type { PhoneDevices } from "../types";
import { PHONE_KINDS, PHONE_STATE_TEXT, phoneState, toggleKind, type PhoneState } from "./phonePush";
import { Skeleton } from "../components/primitives";

/**
 * B8 (flag mobile_push, inside the Android app only): "Notifications on this phone" in Settings -> Alerts.
 * - Android is asked for permission only after the user has read what they will get and pressed Continue; a refusal
 *   is explained (where to allow it in Android), never asked again in a loop.
 * - The four kinds - signals, approvals, exits, renew reminders - each open their screen when tapped.
 * - The text on the phone is a short fixed sentence (no symbol, price or P&L); the details are in the app.
 * The native plug-in is loaded with a dynamic import behind `__ATP_NATIVE__`, so the web bundle never carries it.
 */

async function native() {
  if (!__ATP_NATIVE__) return null;
  try {
    return await import("../native/push");
  } catch {
    return null;
  }
}

function problem(e: unknown, fallback: string): string {
  if (e instanceof ApiError && (e.status === 409 || e.status === 400) && e.detail) return e.detail;
  if (e instanceof Error && e.message && !(e instanceof ApiError)) return e.message;
  return fallback;
}

export default function PhonePushV2() {
  const [data, setData] = useState<PhoneDevices | null>(null);
  const [state, setState] = useState<PhoneState | null>(null);
  const [explaining, setExplaining] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ tone: "ok" | "problem"; text: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.phoneDevices();
      const plugin = await native();
      const permission = plugin ? await plugin.permissionState() : "unavailable";
      setData(next);
      setState(phoneState({ configured: next.configured, available: !!plugin?.phonePushAvailable(), permission }));
    } catch {
      setData(null);
      setState("server-off");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  async function turnOn() {
    setExplaining(false); setBusy("on"); setNote(null);
    try {
      const plugin = await native();
      if (!plugin) throw new Error(PHONE_STATE_TEXT["needs-update"]);
      const got = await plugin.phoneToken();
      if ("refused" in got) { setState("denied"); return; }
      const version = versionLine(await appVersions());
      await api.registerPhone({ token: got.token, platform: "android", label: "This phone", app_version: version.slice(0, 40) });
      setNote({ tone: "ok", text: "Notifications are on for this phone. Send a test to check." });
      await load();
    } catch (e) {
      setNote({ tone: "problem", text: problem(e, "Notifications could not be turned on just now. Try again in a moment.") });
    } finally {
      setBusy(null);
    }
  }

  async function act(id: string, what: "test" | "remove" | string[]) {
    setBusy(id); setNote(null);
    try {
      if (what === "test") {
        const r = await api.testPhone(id);
        setNote({ tone: r.ok ? "ok" : "problem", text: r.detail });
      } else if (what === "remove") {
        await api.removePhone(id);
        setNote({ tone: "ok", text: "This phone gets no more notifications." });
      } else {
        await api.updatePhone(id, { kinds: what });
      }
      await load();
    } catch (e) {
      setNote({ tone: "problem", text: problem(e, "That did not work just now. Try again in a moment.") });
    } finally {
      setBusy(null);
    }
  }

  if (!state) return <Skeleton className="h-20" label="Loading" />;
  const devices = data?.devices ?? [];

  return (
    <div className="space-y-space-3" data-phone-push={state}>
      <div className="flex items-start gap-space-3">
        <Smartphone size={18} aria-hidden className="mt-px shrink-0 text-fg-muted" />
        <p className="min-w-0 flex-1 text-t14 text-fg-muted">
          {state === "ready"
            ? "Get a notification on this phone for your signals, decisions waiting for you, closed positions and broker login reminders. Tapping one opens the right screen."
            : PHONE_STATE_TEXT[state]}
        </p>
      </div>

      {state === "ready" && devices.length === 0 && !explaining && (
        <ActionV2 primary busy={busy === "on"} disabled={busy !== null} onClick={() => setExplaining(true)}>
          <BellRing size={16} aria-hidden /> Turn on notifications
        </ActionV2>
      )}
      {explaining && (
        <div className="space-y-space-2 rounded-control border border-subtle p-space-3" role="group" aria-label="Before Android asks">
          <p className="text-t14 text-fg">Android will ask you to allow notifications from AiAlgoDesk. Each one shows a short sentence only, never a
            symbol, price or amount; the details open inside the app. You can turn them off here or in Android at any time.</p>
          <div className="flex flex-col gap-space-2 sm:flex-row">
            <ActionV2 primary disabled={busy !== null} onClick={() => void turnOn()}>Continue</ActionV2>
            <ActionV2 onClick={() => setExplaining(false)}>Not now</ActionV2>
          </div>
        </div>
      )}

      {devices.map((d) => (
        <fieldset key={d.id} className="space-y-space-2 rounded-control border border-subtle p-space-3" disabled={busy !== null}>
          <legend className="px-space-1 text-t14 font-semibold text-fg">{d.label}</legend>
          {PHONE_KINDS.map((k) => (
            <label key={k.id} className="flex min-h-11 items-center gap-space-2 text-t14 text-fg">
              <input type="checkbox" checked={d.kinds.includes(k.id)} onChange={() => void act(d.id, toggleKind(d.kinds, k.id))} /> {k.label}
            </label>
          ))}
          <div className="flex flex-col gap-space-2 sm:flex-row">
            <ActionV2 busy={busy === d.id} onClick={() => void act(d.id, "test")}>Send a test</ActionV2>
            <ActionV2 onClick={() => void act(d.id, "remove")}>Remove this phone</ActionV2>
          </div>
        </fieldset>
      ))}
      {note && <Note tone={note.tone}>{note.text}</Note>}
    </div>
  );
}
