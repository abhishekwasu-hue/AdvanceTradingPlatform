import { useSyncExternalStore } from "react";

/** PW1: the installable app. Registers the service worker (production builds only - the dev server's modules must
 * never be cached), tracks a waiting new version ("A new version is ready - Reload") and the install offer
 * (Android / desktop `beforeinstallprompt`; iPhone / iPad have no prompt, so they get a short "Add to Home Screen"
 * guide). State lives in this module; components read it with `usePwa()`. */

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

export interface PwaState {
  /** A new version is installed and waiting; `applyUpdate()` switches to it and reloads. */
  updateReady: boolean;
  /** "prompt": the browser can install (button). "ios": show the Add to Home Screen guide. null: nothing to offer. */
  install: "prompt" | "ios" | null;
}

const DISMISS_KEY = "atp_install_dismissed";
const UPDATE_CHECK_MS = 30 * 60_000;

let state: PwaState = { updateReady: false, install: null };
let deferredPrompt: BeforeInstallPromptEvent | null = null;
let waiting: ServiceWorker | null = null;
const listeners = new Set<() => void>();

function set(patch: Partial<PwaState>) {
  state = { ...state, ...patch };
  listeners.forEach((l) => l());
}

/** True when the page already runs as the installed app (no install offer then). */
export function isStandalone(win: Window = window): boolean {
  const nav = win.navigator as Navigator & { standalone?: boolean };
  return nav.standalone === true || (typeof win.matchMedia === "function" && win.matchMedia("(display-mode: standalone)").matches);
}

/** iPhone / iPad Safari (iPadOS reports a Mac with touch). Other iOS browsers cannot add to the home screen before
 * iOS 16.4 and say so themselves; the guide names Safari's Share button. */
export function isIosSafari(ua: string, maxTouchPoints = 0): boolean {
  const ios = /iphone|ipad|ipod/i.test(ua) || (/macintosh/i.test(ua) && maxTouchPoints > 1);
  return ios && /safari/i.test(ua) && !/crios|fxios|edgios|opios/i.test(ua);
}

function dismissed(): boolean {
  try { return localStorage.getItem(DISMISS_KEY) === "1"; } catch { return false; }
}

export function dismissInstall(): void {
  try { localStorage.setItem(DISMISS_KEY, "1"); } catch { /* storage unavailable: hidden for this visit only */ }
  set({ install: null });
}

export async function promptInstall(): Promise<void> {
  const event = deferredPrompt;
  if (!event) return;
  deferredPrompt = null;
  await event.prompt();
  const choice = await event.userChoice.catch(() => ({ outcome: "dismissed" as const }));
  if (choice.outcome === "dismissed") dismissInstall();
  else set({ install: null });
}

export function applyUpdate(): void {
  if (!waiting) { window.location.reload(); return; }
  waiting.postMessage({ type: "SKIP_WAITING" });     // the page reloads on `controllerchange` (below)
}

function watchRegistration(reg: ServiceWorkerRegistration) {
  const offer = (worker: ServiceWorker | null) => {
    // Only an update: the very first install has no controller and nothing to reload into.
    if (worker && navigator.serviceWorker.controller) { waiting = worker; set({ updateReady: true }); }
  };
  offer(reg.waiting);
  reg.addEventListener("updatefound", () => {
    const worker = reg.installing;
    worker?.addEventListener("statechange", () => { if (worker.state === "installed") offer(worker); });
  });
  const check = () => { reg.update().catch(() => undefined); };
  window.setInterval(check, UPDATE_CHECK_MS);
  document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") check(); });
}

let started = false;

/** Called once from main.tsx. */
export function initPwa(): void {
  if (started || typeof window === "undefined") return;
  started = true;

  window.addEventListener("beforeinstallprompt", (e) => {
    e.preventDefault();                                // our own button, not the browser's mini-infobar
    deferredPrompt = e as BeforeInstallPromptEvent;
    if (!dismissed() && !isStandalone()) set({ install: "prompt" });
  });
  window.addEventListener("appinstalled", () => { deferredPrompt = null; set({ install: null }); });
  if (!dismissed() && !isStandalone() && isIosSafari(navigator.userAgent, navigator.maxTouchPoints)) set({ install: "ios" });

  if (!import.meta.env.PROD || !("serviceWorker" in navigator)) return;
  let reloading = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (reloading || !state.updateReady) return;       // first install claims the page: no reload needed
    reloading = true;
    window.location.reload();
  });
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").then(watchRegistration).catch(() => undefined);
  });
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

export function usePwa(): PwaState {
  return useSyncExternalStore(subscribe, () => state, () => state);
}

/** Tests only. */
export function _setPwaState(patch: Partial<PwaState>): void { set(patch); }
