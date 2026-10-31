// PW6: install/update metrics. Each event is two words from closed lists - what happened and the platform family - and
// the server only counts it (POST /api/pwa/events, no login, no user id, no free text, nothing stored). Sending is
// fire-and-forget: a failure is ignored and never reaches the UI.
//
// Independent of the PW1 service-worker/install-button module: it listens to the browser's own events.
//  - `appinstalled`                        -> installed
//  - `beforeinstallprompt`                 -> install_prompt_shown (once per browser session; the browser offered
//                                             install), then the event's own `userChoice` -> install_prompt_accepted |
//                                             install_prompt_dismissed (when anything calls prompt() on it; this
//                                             module never calls prompt() or preventDefault())
//  - opened in standalone display mode     -> launched_standalone (once per browser session)
//  - installed app opens a new version     -> update_applied (this device had opened an older version before)

import { apiUrl } from "../api/base";

export const PWA_EVENTS = [
  "installed", "launched_standalone", "update_applied", "install_prompt_shown", "install_prompt_accepted", "install_prompt_dismissed",
] as const;
export type PwaEvent = (typeof PWA_EVENTS)[number];
export type PwaPlatform = "android" | "ios" | "desktop" | "other";

const ENDPOINT = apiUrl("/pwa/events");
const VERSION_KEY = "atp_pwa_last_version";
const LAUNCH_KEY = "atp_pwa_launch_reported";
const PROMPT_KEY = "atp_pwa_prompt_reported";

/** The platform family from the User-Agent (iPadOS reports a Mac UA; touch points tell it apart). */
export function detectPlatform(userAgent: string, maxTouchPoints = 0): PwaPlatform {
  const ua = userAgent || "";
  if (/android/i.test(ua)) return "android";
  if (/iphone|ipad|ipod/i.test(ua) || (/macintosh/i.test(ua) && maxTouchPoints > 1)) return "ios";
  if (/windows|macintosh|mac os x|cros|linux|x11/i.test(ua)) return "desktop";
  return "other";
}

/** True when the previous version this device opened is known and differs from the running one. */
export function isUpdate(previous: string | null, current: string): boolean {
  return !!previous && !!current && previous !== current;
}

export function appVersion(): string {
  return typeof __APP_VERSION__ === "string" && __APP_VERSION__ ? __APP_VERSION__ : "dev";
}

function currentPlatform(): PwaPlatform {
  try {
    return detectPlatform(navigator.userAgent, navigator.maxTouchPoints ?? 0);
  } catch {
    return "other";
  }
}

export function sendPwaEvent(event: PwaEvent, platform: PwaPlatform = currentPlatform()): void {
  try {
    void fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ event, platform }),
      credentials: "omit",
      keepalive: true,
    }).catch(() => {});  // intentional: metrics are best effort
  } catch {
    /* intentional: fetch unavailable - metrics are best effort */
  }
}

function storageGet(storage: () => Storage, key: string): string | null {
  try { return storage().getItem(key); } catch { return null; }
}

function storageSet(storage: () => Storage, key: string, value: string): void {
  try { storage().setItem(key, value); } catch { /* intentional: storage unavailable */ }
}

export function isStandalone(win: Window = window): boolean {
  try {
    if (win.matchMedia?.("(display-mode: standalone)").matches) return true;
  } catch { /* intentional: no matchMedia */ }
  return (win.navigator as Navigator & { standalone?: boolean }).standalone === true;   // iOS Safari home-screen app
}

interface InstallPromptEvent extends Event { userChoice?: Promise<{ outcome?: string }> }

/** Wires the listeners and reports the launch/update events of this page load. Returns a cleanup function. */
export function startPwaEvents(win: Window = window, send: (event: PwaEvent) => void = sendPwaEvent): () => void {
  const local = () => win.localStorage;
  const session = () => win.sessionStorage;

  const standalone = isStandalone(win);
  const version = appVersion();
  // Only the installed app reports updates (a browser tab picking up a new build is not a PWA update).
  if (standalone && isUpdate(storageGet(local, VERSION_KEY), version)) send("update_applied");
  storageSet(local, VERSION_KEY, version);

  if (standalone && storageGet(session, LAUNCH_KEY) !== "1") {
    storageSet(session, LAUNCH_KEY, "1");
    send("launched_standalone");
  }

  const onPrompt = (e: Event) => {
    // Chrome fires this on every page load while the app is installable: count one offer per browser session.
    if (storageGet(session, PROMPT_KEY) !== "1") {
      storageSet(session, PROMPT_KEY, "1");
      send("install_prompt_shown");
    }
    const choice = (e as InstallPromptEvent).userChoice;
    if (choice && typeof choice.then === "function") {
      choice.then((c) => {
        if (c?.outcome === "accepted") send("install_prompt_accepted");
        else if (c?.outcome === "dismissed") send("install_prompt_dismissed");
      }).catch(() => {});  // intentional: metrics are best effort
    }
  };
  const onInstalled = () => send("installed");
  win.addEventListener("beforeinstallprompt", onPrompt);
  win.addEventListener("appinstalled", onInstalled);
  return () => {
    win.removeEventListener("beforeinstallprompt", onPrompt);
    win.removeEventListener("appinstalled", onInstalled);
  };
}
