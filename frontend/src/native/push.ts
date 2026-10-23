import { Capacitor, registerPlugin, type PluginListenerHandle } from "@capacitor/core";

/**
 * B8: phone notifications in the Android app (docs/design/MOBILE_PUSH.md). Loaded only inside the app (a dynamic
 * import behind `__ATP_NATIVE__`), so the web bundle never carries it.
 *
 * The native side is Capacitor's push plug-in (`PushNotifications`). It is reached through `registerPlugin`, so this
 * file builds and runs before the plug-in is installed: until then `phonePushAvailable()` is false and Settings says
 * notifications arrive with an app update. Installing it (with the Firebase project, MA-2) is one step: add the pinned
 * `@capacitor/push-notifications`, put `google-services.json` in place in CI, `npx cap sync android`.
 */

interface PermissionStatus { receive: "prompt" | "prompt-with-rationale" | "granted" | "denied" }
interface Token { value: string }
interface ActionPerformed { notification: { data?: unknown } }
interface Channel { id: string; name: string; description?: string; importance?: 1 | 2 | 3 | 4 | 5; sound?: string; vibration?: boolean }

interface PushNotificationsPlugin {
  checkPermissions(): Promise<PermissionStatus>;
  requestPermissions(): Promise<PermissionStatus>;
  register(): Promise<void>;
  unregister(): Promise<void>;
  createChannel(channel: Channel): Promise<void>;
  addListener(event: "registration", fn: (token: Token) => void): Promise<PluginListenerHandle>;
  addListener(event: "registrationError", fn: (error: { error: string }) => void): Promise<PluginListenerHandle>;
  addListener(event: "pushNotificationActionPerformed", fn: (action: ActionPerformed) => void): Promise<PluginListenerHandle>;
}

const PLUGIN = "PushNotifications";
const PushNotifications = registerPlugin<PushNotificationsPlugin>(PLUGIN);

/** The Android notification channels: one per kind (so the user can tune each in Android settings) and a silent one
 * the server uses inside the organisation's quiet hours. */
export const ANDROID_CHANNELS: Channel[] = [
  { id: "signals", name: "Signals", description: "Signals from your own strategies", importance: 4 },
  { id: "approvals", name: "Decisions", description: "Decisions waiting for you", importance: 4 },
  { id: "exits", name: "Positions closed", description: "When a position was closed", importance: 4 },
  { id: "renew", name: "Broker login", description: "Reminders to renew your broker login", importance: 4 },
  { id: "quiet", name: "Quiet hours", description: "Notifications during your quiet hours, without sound", importance: 2, vibration: false },
];

export function phonePushAvailable(): boolean {
  try {
    return Capacitor.isPluginAvailable(PLUGIN);
  } catch {
    return false;
  }
}

export async function permissionState(): Promise<PermissionStatus["receive"] | "unavailable"> {
  if (!phonePushAvailable()) return "unavailable";
  try {
    return (await PushNotifications.checkPermissions()).receive;
  } catch {
    return "unavailable";
  }
}

/** Asks Android for permission (after the explain-first screen), registers with FCM and resolves the token. Never
 * re-asks after a refusal: a "denied" answer is returned and Settings explains how to allow it in Android. */
export async function phoneToken(timeoutMs = 15_000): Promise<{ token: string } | { refused: true }> {
  let status = await PushNotifications.checkPermissions();
  if (status.receive === "prompt" || status.receive === "prompt-with-rationale") status = await PushNotifications.requestPermissions();
  if (status.receive !== "granted") return { refused: true };
  for (const channel of ANDROID_CHANNELS) {
    try { await PushNotifications.createChannel(channel); } catch { /* older Android: channels do not exist */ }
  }
  const handles: PluginListenerHandle[] = [];
  try {
    const token = await new Promise<string>((resolve, reject) => {
      const timer = window.setTimeout(() => reject(new Error("The phone did not answer in time. Try again.")), timeoutMs);
      void PushNotifications.addListener("registration", (t) => { window.clearTimeout(timer); resolve(t.value); }).then((h) => handles.push(h));
      void PushNotifications.addListener("registrationError", () => { window.clearTimeout(timer); reject(new Error("This phone could not turn on notifications. Try again later.")); }).then((h) => handles.push(h));
      void PushNotifications.register().catch(reject);
    });
    return { token };
  } finally {
    for (const h of handles) void h.remove();
  }
}

/** A tap on a notification: `open` gets the notification's data block (src/native/links.ts checks its path). */
export async function onNotificationTap(open: (data: unknown) => void): Promise<PluginListenerHandle | null> {
  if (!phonePushAvailable()) return null;
  try {
    return await PushNotifications.addListener("pushNotificationActionPerformed", (action) => open(action.notification?.data));
  } catch {
    return null;
  }
}

export async function stopPhonePush(): Promise<void> {
  if (!phonePushAvailable()) return;
  try { await PushNotifications.unregister(); } catch { /* nothing registered */ }
}
