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
  /** "prompt": the browser can install (button). "ios": show the Add to Home Screen guide. "unavailable": install is
   * impossible on this address (see `blocked`) - say so instead of failing silently. null: nothing to offer. */
  install: "prompt" | "ios" | "unavailable" | null;
  /** Why this browser cannot install the app here, when known (see installBlockReason). */
  blocked: InstallBlock | null;
  /** True while the browser's own install prompt is waiting to be shown (`beforeinstallprompt` arrived). */
  canPrompt: boolean;
}

/**
 * Why the app cannot be installed from this page (owner mobile brief, Phase A):
 * - "insecure": the page is not a secure context (plain http on a bare IP or a domain without a certificate);
 * - "certificate": the address is https, but the browser did not trust its certificate (a bare IP with Caddy's internal
 *   certificate) - the service worker that installing needs was refused;
 * - "no-service-worker": this browser has no service workers (an old browser, some private windows).
 * The first two need the owner (a domain with a trusted certificate); the last needs another browser.
 */
export type InstallBlock = "insecure" | "certificate" | "no-service-worker";

/** The plain message for an address that cannot install the app (owner brief, word for word). */
export const INSTALL_NEEDS_DOMAIN =
  "Install needs the app's web address (domain) with a trusted certificate. Ask the owner to set up the domain.";
export const INSTALL_NEEDS_BROWSER =
  "This browser cannot install the app. Open this page in Chrome, Edge or Safari to install it.";
export const INSTALL_NOT_OFFERED =
  "Your browser has not offered to install the app yet. Open the browser menu and choose \"Install app\" or \"Add to Home screen\".";
export const INSTALL_IOS =
  "On iPhone or iPad: tap Share in Safari, then Add to Home Screen.";
export const INSTALL_DONE = "The app is installed on this device.";

/** What the page can tell before trying: not a secure context, or no service workers at all. */
export function installBlockReason(win: Pick<Window, "isSecureContext" | "navigator">): InstallBlock | null {
  if (win.isSecureContext === false) return "insecure";
  if (!("serviceWorker" in win.navigator)) return "no-service-worker";
  return null;
}

/** True when a failed service-worker registration was refused because the certificate is not trusted. */
export function isCertificateError(error: unknown): boolean {
  const text = error instanceof Error ? `${error.name} ${error.message}` : String(error ?? "");
  return /\b(SSL|TLS|certificate)\b/i.test(text);
}

/** The install entry's answer (More sheet "Install the app"): prompt when the browser offers it, otherwise the plain
 * reason - never nothing. */
export type InstallHelp = { kind: "prompt" } | { kind: "message"; text: string };

export function installHelp(s: { blocked: InstallBlock | null; canPrompt: boolean; ios: boolean; standalone: boolean }): InstallHelp {
  if (s.standalone) return { kind: "message", text: INSTALL_DONE };
  if (s.blocked === "insecure" || s.blocked === "certificate") return { kind: "message", text: INSTALL_NEEDS_DOMAIN };
  if (s.canPrompt) return { kind: "prompt" };
  if (s.ios) return { kind: "message", text: INSTALL_IOS };
  if (s.blocked === "no-service-worker") return { kind: "message", text: INSTALL_NEEDS_BROWSER };
  return { kind: "message", text: INSTALL_NOT_OFFERED };
}

/** The banner state at start-up: an address that can never install says so (once, dismissable) instead of staying
 * silent; otherwise the iPhone guide as before. A browser without service workers is not nagged (the entry explains). */
export function initialInstall(s: { blocked: InstallBlock | null; dismissed: boolean; standalone: boolean; ios: boolean }): PwaState["install"] {
  if (s.dismissed || s.standalone) return null;
  if (s.blocked === "insecure" || s.blocked === "certificate") return "unavailable";
  return s.ios ? "ios" : null;
}

const DISMISS_KEY = "atp_install_dismissed";
const UPDATE_CHECK_MS = 30 * 60_000;

let state: PwaState = { updateReady: false, install: null, blocked: null, canPrompt: false };
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
  set({ canPrompt: false });
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
    set({ canPrompt: true, blocked: null });
    if (!dismissed() && !isStandalone()) set({ install: "prompt" });
  });
  window.addEventListener("appinstalled", () => { deferredPrompt = null; set({ install: null, canPrompt: false }); });
  const blocked = installBlockReason(window);
  if (blocked) set({ blocked });
  const first = initialInstall({ blocked, dismissed: dismissed(), standalone: isStandalone(), ios: isIosSafari(navigator.userAgent, navigator.maxTouchPoints) });
  if (first) set({ install: first });

  if (!import.meta.env.PROD || !("serviceWorker" in navigator)) return;
  let reloading = false;
  navigator.serviceWorker.addEventListener("controllerchange", () => {
    if (reloading || !state.updateReady) return;       // first install claims the page: no reload needed
    reloading = true;
    window.location.reload();
  });
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").then(watchRegistration).catch((e: unknown) => {
      // A bare IP with an untrusted (internal) certificate: the worker is refused, so the app can never install here.
      if (!isCertificateError(e) || state.canPrompt) return;
      set({ blocked: "certificate" });
      if (!dismissed() && !isStandalone() && state.install !== "prompt") set({ install: "unavailable" });
    });
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
