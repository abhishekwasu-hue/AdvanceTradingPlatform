/** PW3: what this device can do about web push, decided before the browser is asked anything. */
export type PushReadiness =
  | "unsupported"        // no service worker / Push API (old browser, private mode in some browsers)
  | "ios-install-first"  // iPhone / iPad: push works only in the app added to the Home Screen (iOS 16.4+)
  | "denied"             // the user blocked notifications for this site: only the browser settings can undo it
  | "ask"                // supported, not decided yet: explain first, then ask
  | "granted";           // permission given (this device may or may not be subscribed yet)

export interface PushEnv {
  hasServiceWorker: boolean;
  hasPushManager: boolean;
  permission: NotificationPermission | "unavailable";
  ios: boolean;
  standalone: boolean;
}

export function pushReadiness(env: PushEnv): PushReadiness {
  if (env.ios && !env.standalone) return "ios-install-first";
  if (!env.hasServiceWorker || !env.hasPushManager || env.permission === "unavailable") return "unsupported";
  if (env.permission === "denied") return "denied";
  if (env.permission === "granted") return "granted";
  return "ask";
}

export function currentPushEnv(): PushEnv {
  const nav = navigator as Navigator & { standalone?: boolean };
  const ua = navigator.userAgent;
  const ios = /iphone|ipad|ipod/i.test(ua) || (/macintosh/i.test(ua) && navigator.maxTouchPoints > 1);
  return {
    hasServiceWorker: "serviceWorker" in navigator,
    hasPushManager: typeof window !== "undefined" && "PushManager" in window,
    permission: typeof Notification === "undefined" ? "unavailable" : Notification.permission,
    ios,
    standalone: nav.standalone === true || (typeof window.matchMedia === "function" && window.matchMedia("(display-mode: standalone)").matches),
  };
}

/** The push categories (backend app/alerts/deeplink.py PUSH_CATEGORIES) in the order the settings list them. */
export const PUSH_CATEGORIES: { id: string; label: string }[] = [
  { id: "proposals", label: "Proposals waiting for your decision" },
  { id: "trades", label: "Entries, exits and rejected orders" },
  { id: "alerts", label: "Screen, OI banner and news alerts" },
  { id: "risk", label: "Risk limits and the daily loss limit" },
  { id: "account", label: "Broker login, connection, security and system problems" },
  { id: "reports", label: "End-of-day and weekly reports" },
];

/** null on the channel = every category. */
export function selectedCategories(stored: unknown): Set<string> {
  return new Set(Array.isArray(stored) ? (stored as string[]) : PUSH_CATEGORIES.map((c) => c.id));
}

/** Base64url VAPID key -> the bytes `pushManager.subscribe` wants. */
export function vapidKeyBytes(publicKey: string): Uint8Array<ArrayBuffer> {
  const padded = publicKey.replace(/-/g, "+").replace(/_/g, "/") + "=".repeat((4 - (publicKey.length % 4)) % 4);
  const text = atob(padded);
  const bytes = new Uint8Array(new ArrayBuffer(text.length));
  for (let i = 0; i < text.length; i++) bytes[i] = text.charCodeAt(i);
  return bytes;
}
