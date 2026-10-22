import { useCallback, useEffect, useState } from "react";
import { api } from "../api/client";
import { ApiError, NETWORK_MESSAGE } from "../api/errors";
import { useAuth } from "../auth/AuthContext";
import type { BrokerTokenInfo } from "../types";

/**
 * UI2-0 (owner brief ATP_SESSION_NO_SAMPLE_DATA; UI v2 proposal §5 empty states, §10 daily login): the signed-in
 * user's broker state, read from the existing GET /api/broker/token-status (one row per stored credential, with
 * `needs_login` and the login method flags). No new backend route. Nothing here places, changes or cancels an order:
 * the only writes are the existing broker login calls, started by the trader's own click.
 */

/** How "Log in to <Broker>" works for a broker today (§10 login method key). */
export type BrokerLoginMethod =
  | "oauth"          // Upstox: the platform drives the login and captures the code (GET /broker/upstox/oauth/start)
  | "login-link"     // Zerodha, Fyers: open the broker's login page, then paste the code it returns (login-url / login-code)
  | "authenticate";  // everyone else: the platform logs in with the stored keys (POST /broker/{name}/authenticate)

const DISPLAY_NAMES: Record<string, string> = {
  upstox: "Upstox", zerodha: "Zerodha", fyers: "Fyers", angel_one: "Angel One", shoonya: "Shoonya", dhan: "Dhan",
  coindcx: "CoinDCX",
};

/** The broker's name as a trader knows it: "angel_one" -> "Angel One". An unknown id is title-cased. */
export function brokerDisplayName(name: string): string {
  return DISPLAY_NAMES[name] ?? name.split(/[_\s-]+/).filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
}

/** Brokers the platform logs in to with a stored PIN or password plus a TOTP secret (§10, UI-6). For these the
 * "we never see or store your broker password" sentence is NEVER used: Angel One stores the PIN, Shoonya the password. */
export const TOTP_BROKERS: ReadonlySet<string> = new Set(["angel_one", "shoonya"]);

/** The login method for a stored credential. The server's flags decide; the broker id is the fallback for an old row. */
export function loginMethodFor(t: Pick<BrokerTokenInfo, "broker_name" | "oauth_supported" | "login_url_supported">): BrokerLoginMethod {
  if (t.oauth_supported && t.broker_name === "upstox") return "oauth";
  if (t.login_url_supported) return "login-link";
  if (t.oauth_supported === undefined && t.broker_name === "upstox") return "oauth";
  if (t.login_url_supported === undefined && (t.broker_name === "zerodha" || t.broker_name === "fyers")) return "login-link";
  return "authenticate";
}

/** The "never see or store your broker password" promise: true only for brokers whose login happens on the broker's
 * own page (OAuth, login link). Never for TOTP brokers (UI-6), whatever the server's flags say. */
export const NEVER_STORE_PASSWORD =
  "Brokers end the session every trading day. Log in once each morning; we never see or store your broker password.";

/** The one line beside "Log in to <Broker>" (§10 "Daily login, renamed"). */
export function loginNote(t: Pick<BrokerTokenInfo, "broker_name" | "oauth_supported" | "login_url_supported">): string {
  const name = brokerDisplayName(t.broker_name);
  if (t.broker_name === "angel_one") return `We log in to ${name} for you with the PIN and authenticator key you stored, encrypted.`;
  if (t.broker_name === "shoonya") return `We log in to ${name} for you with the password and authenticator key you stored, encrypted.`;
  if (TOTP_BROKERS.has(t.broker_name)) return `We log in to ${name} for you with the details you stored, encrypted.`;
  if (t.broker_name === "dhan") return `We log in with the access token you stored. If ${name} has ended it, renew it on the Broker page.`;
  if (t.broker_name === "coindcx") return `We log in with the API key you stored. ${name} needs no daily login.`;
  if (loginMethodFor(t) === "authenticate") return `We log in to ${name} with the keys you stored, encrypted.`;
  return NEVER_STORE_PASSWORD;
}

// --- The gate's state ----------------------------------------------------------------------------------------------

export type BrokerGateState =
  | { kind: "loading" }
  | { kind: "signed-out" }
  /** The broker status could not be read. Never treated as "connected": no data is shown in its place. */
  | { kind: "unavailable" }
  /** No broker credential stored for this organisation. */
  | { kind: "no-broker" }
  /** Credentials stored, but no session is usable today. `broker` gets the primary action, `others` a quiet one each. */
  | { kind: "login-needed"; broker: BrokerTokenInfo; others: BrokerTokenInfo[]; expired: boolean }
  /** At least one broker session is usable: the page renders its real data. */
  | { kind: "connected"; brokers: BrokerTokenInfo[] };

export interface BrokerGateInput {
  authLoading: boolean;
  signedIn: boolean;
  /** null until the first answer. */
  tokens: BrokerTokenInfo[] | null;
  failed: boolean;
}

const label = (t: BrokerTokenInfo) => t.account_label ?? "primary";

/** The gate's state from what is known. Order: signed out, loading, failed, none stored, connected, login needed. */
export function brokerGateState(input: BrokerGateInput): BrokerGateState {
  if (input.authLoading) return { kind: "loading" };
  if (!input.signedIn) return { kind: "signed-out" };
  if (input.failed) return { kind: "unavailable" };
  if (input.tokens === null) return { kind: "loading" };
  if (input.tokens.length === 0) return { kind: "no-broker" };
  const usable = input.tokens.filter((t) => !t.needs_login);
  if (usable.length > 0) return { kind: "connected", brokers: usable };
  // The primary account first, then the order the server listed them in.
  const ordered = [...input.tokens].sort((a, b) => Number(label(b) === "primary") - Number(label(a) === "primary"));
  const [broker, ...others] = ordered;
  return { kind: "login-needed", broker, others, expired: broker.token_status === "EXPIRED" };
}

// --- Starting a login ----------------------------------------------------------------------------------------------

export interface LoginDeps {
  upstoxOAuthStart: (accountLabel: string) => Promise<{ authorization_url: string }>;
  brokerLoginUrl: (name: string, accountLabel: string) => Promise<{ authorization_url: string }>;
  authenticateBroker: (name: string, accountLabel: string) => Promise<unknown>;
  /** Opens a blank tab synchronously inside the click (a tab opened after an await is blocked as a popup). */
  openTab: () => Window | null;
  /** Sends this tab to the broker (OAuth). */
  redirect: (url: string) => void;
}

export type LoginOutcome =
  | { kind: "redirected" }                                 // OAuth: the browser is on its way to the broker
  | { kind: "code-needed"; link: string; opened: boolean } // login link: paste the code the broker shows
  | { kind: "logged-in" };                                 // authenticate: the session is live now

/**
 * Starts "Log in to <Broker>" with the existing login path for that broker type (§10). The part before the first
 * await runs inside the click, so the login-link tab is not blocked as a popup.
 */
export async function startBrokerLogin(t: BrokerTokenInfo, deps: LoginDeps): Promise<LoginOutcome> {
  const method = loginMethodFor(t);
  if (method === "oauth") {
    const { authorization_url } = await deps.upstoxOAuthStart(label(t));
    deps.redirect(authorization_url);
    return { kind: "redirected" };
  }
  if (method === "login-link") {
    const tab = deps.openTab();
    if (tab) tab.opener = null;
    try {
      const { authorization_url } = await deps.brokerLoginUrl(t.broker_name, label(t));
      if (tab) tab.location.href = authorization_url;
      return { kind: "code-needed", link: authorization_url, opened: !!tab };
    } catch (e) {
      tab?.close();
      throw e;
    }
  }
  await deps.authenticateBroker(t.broker_name, label(t));
  return { kind: "logged-in" };
}

/** The real dependencies: the existing API client and the browser. */
export const BROWSER_LOGIN_DEPS: LoginDeps = {
  upstoxOAuthStart: (accountLabel) => api.upstoxOAuthStart(accountLabel),
  brokerLoginUrl: (name, accountLabel) => api.brokerLoginUrl(name, accountLabel),
  authenticateBroker: (name, accountLabel) => api.authenticateBroker(name, accountLabel),
  openTab: () => window.open("about:blank", "_blank"),
  redirect: (url) => { window.location.href = url; },
};

/** A failed login as "what happened + what to do next" (§3). Always one of the fixed sentences below: the server's own
 * text is never shown (it may name internals); the status, and for 403 whether a fresh two-factor check is asked
 * for, only choose the sentence. */
export function loginErrorText(error: unknown, brokerName: string): string {
  const name = brokerDisplayName(brokerName);
  if (error instanceof ApiError) {
    if (error.status === 0 || error.message === NETWORK_MESSAGE) return NETWORK_MESSAGE;
    switch (error.status) {
      case 400: return `Your ${name} keys are incomplete. Check them on the Broker page, then try again.`;
      case 401: return `Your sign-in has ended. Sign in again, then log in to ${name}.`;
      case 403:
        return /two-factor|step-up|authenticator/i.test(`${error.detail} ${error.body}`)
          ? `Logging in to ${name} needs a fresh two-factor check. Confirm it with your authenticator app, then try again.`
          : `You do not have permission to log in to ${name}. Ask the owner of your organisation.`;
      case 404: return `Your ${name} keys are no longer stored. Connect ${name} again on the Broker page.`;
      case 409: return `Your ${name} connection changed while you were logging in. Try again.`;
      case 429: return "Too many attempts. Wait a minute, then try again.";
      case 502: return `${name} did not accept the login. Try again; if it repeats, check your ${name} keys on the Broker page.`;
      default: return `The ${name} login did not finish. Try again in a moment.`;
    }
  }
  if (error instanceof TypeError) return NETWORK_MESSAGE;
  return `The ${name} login did not finish. Try again in a moment.`;
}

// --- The hook ------------------------------------------------------------------------------------------------------

/** A re-check that fails keeps the last good answer for this long (and for one failure); after that the gate says
 * the check failed. The first check of a session fails closed at once. */
export const STALE_AFTER_MS = 5 * 60 * 1000;
export const FAILURES_BEFORE_UNAVAILABLE = 2;

/** True when the gate must say "We could not check your broker connection" instead of using what it knows. */
export function brokerCheckFailed(input: { hasGoodAnswer: boolean; consecutiveFailures: number; ageMs: number }): boolean {
  if (input.consecutiveFailures === 0) return false;
  if (!input.hasGoodAnswer) return true;                                    // first load: fail closed
  return input.consecutiveFailures >= FAILURES_BEFORE_UNAVAILABLE || input.ageMs > STALE_AFTER_MS;
}

export interface BrokerSession {
  state: BrokerGateState;
  /** Reads the broker status again (after a login, or when the trader asks). */
  refresh: () => void;
  /** UI2-2: this user's token-status rows as last read (null until the first answer) - the dashboard tells "2 brokers ·
   * 1 needs login" from them. */
  rows: BrokerTokenInfo[] | null;
}

/** What is known for one user: the last good answer and the failed checks since. Keyed by user, so another
 * account never sees the previous account's brokers, not even for one render. */
export interface Known { userId: number | null; rows: BrokerTokenInfo[] | null; at: number; failures: number }
const UNKNOWN: Known = { userId: null, rows: null, at: 0, failures: 0 };

/** What is known for `userId`: another user's answer (or none) is nothing - the gate shows "checking". */
export function knownFor(known: Known, userId: number | null): Known {
  return userId !== null && known.userId === userId ? known : UNKNOWN;
}

/** The signed-in user's broker state, re-read when the user changes, on `refresh()`, and when this tab is shown again
 * (a login finished in another tab). A background re-check keeps the last good answer (see brokerCheckFailed). */
export function useBrokerSession(): BrokerSession {
  const { user, loading } = useAuth();
  const userId = user?.id ?? null;
  const [known, setKnown] = useState<Known>(UNKNOWN);
  const [nonce, setNonce] = useState(0);
  const refresh = useCallback(() => setNonce((n) => n + 1), []);

  useEffect(() => {
    if (userId === null) return;
    let live = true;
    api.brokerTokenStatus()
      .then((rows) => { if (live) setKnown({ userId, rows: Array.isArray(rows) ? rows : [], at: Date.now(), failures: 0 }); })
      .catch(() => {
        if (live) setKnown((k) => (k.userId === userId ? { ...k, failures: k.failures + 1 } : { userId, rows: null, at: 0, failures: 1 }));
      });
    return () => { live = false; };
  }, [userId, nonce]);

  useEffect(() => {
    const onShow = () => { if (document.visibilityState === "visible") refresh(); };
    document.addEventListener("visibilitychange", onShow);
    return () => document.removeEventListener("visibilitychange", onShow);
  }, [refresh]);

  const mine = knownFor(known, userId);
  const failed = brokerCheckFailed({ hasGoodAnswer: mine.rows !== null, consecutiveFailures: mine.failures, ageMs: Date.now() - mine.at });
  return { state: brokerGateState({ authLoading: loading, signedIn: userId !== null, tokens: mine.rows, failed }), refresh, rows: mine.rows };
}
