import { describe, expect, it, vi } from "vitest";
import { ApiError, NETWORK_MESSAGE } from "../api/errors";
import { tokenRow } from "./brokerRows.fixture";
import {
  FAILURES_BEFORE_UNAVAILABLE, STALE_AFTER_MS, brokerCheckFailed, knownFor,
  NEVER_STORE_PASSWORD, TOTP_BROKERS, brokerDisplayName, brokerGateState, loginErrorText, loginMethodFor, loginNote,
  startBrokerLogin, type LoginDeps,
} from "./brokerSession";

const base = { authLoading: false, signedIn: true, failed: false };

describe("UI2-0 broker gate state", () => {
  it("no broker stored -> connect", () => {
    expect(brokerGateState({ ...base, tokens: [] })).toEqual({ kind: "no-broker" });
  });

  it("stored but no usable session -> log in, the primary account first", () => {
    const second = tokenRow("zerodha", { account_label: "second" });
    const primary = tokenRow("upstox");
    const s = brokerGateState({ ...base, tokens: [second, primary] });
    expect(s.kind).toBe("login-needed");
    if (s.kind !== "login-needed") return;
    expect(s.broker).toBe(primary);
    expect(s.others).toEqual([second]);
    expect(s.expired).toBe(true);
    const unknown = brokerGateState({ ...base, tokens: [tokenRow("upstox", { token_status: "UNKNOWN" })] });
    expect(unknown.kind === "login-needed" && unknown.expired).toBe(false);
  });

  it("any usable session -> connected", () => {
    const ok = tokenRow("upstox", { needs_login: false, token_status: "VALID" });
    expect(brokerGateState({ ...base, tokens: [tokenRow("zerodha"), ok] })).toEqual({ kind: "connected", brokers: [ok] });
  });

  it("never 'connected' while unknown: loading, signed out or a failed check", () => {
    expect(brokerGateState({ ...base, tokens: null }).kind).toBe("loading");
    expect(brokerGateState({ ...base, authLoading: true, tokens: [] }).kind).toBe("loading");
    expect(brokerGateState({ ...base, signedIn: false, tokens: null }).kind).toBe("signed-out");
    expect(brokerGateState({ ...base, failed: true, tokens: [tokenRow("upstox", { needs_login: false })] }).kind).toBe("unavailable");
  });
});

describe("UI2-0 broker names and login methods (§10)", () => {
  it("shows display names", () => {
    expect(brokerDisplayName("angel_one")).toBe("Angel One");
    expect(brokerDisplayName("coindcx")).toBe("CoinDCX");
    expect(brokerDisplayName("upstox")).toBe("Upstox");
    expect(brokerDisplayName("new_broker")).toBe("New Broker");
  });

  it("OAuth for Upstox, the login link for Zerodha and Fyers, authenticate for everyone else", () => {
    expect(loginMethodFor(tokenRow("upstox"))).toBe("oauth");
    expect(loginMethodFor(tokenRow("zerodha"))).toBe("login-link");
    expect(loginMethodFor(tokenRow("fyers"))).toBe("login-link");
    for (const b of ["angel_one", "shoonya", "dhan", "coindcx"]) expect(loginMethodFor(tokenRow(b)), b).toBe("authenticate");
    // An old row without the flags: the broker id decides.
    expect(loginMethodFor({ broker_name: "upstox", oauth_supported: undefined as unknown as boolean, login_url_supported: undefined })).toBe("oauth");
    expect(loginMethodFor({ broker_name: "fyers", oauth_supported: false, login_url_supported: undefined })).toBe("login-link");
  });

  it("never promises 'we never store your password' for a TOTP broker", () => {
    for (const b of TOTP_BROKERS) {
      const note = loginNote(tokenRow(b));
      expect(note, b).not.toMatch(/never .*store/i);
      expect(note, b).not.toMatch(/password\b.*never/i);
      expect(note, b).toMatch(/authenticator key you stored, encrypted/);
    }
    // ...even if a server row claimed a hosted login for one.
    expect(loginNote(tokenRow("angel_one", { login_url_supported: true }))).not.toBe(NEVER_STORE_PASSWORD);
    expect(loginNote(tokenRow("shoonya"))).toMatch(/password and authenticator key/);
    expect(loginNote(tokenRow("angel_one"))).toMatch(/PIN and authenticator key/);
  });

  it("uses the 'never store your password' line for OAuth and login-link brokers", () => {
    for (const b of ["upstox", "zerodha", "fyers"]) expect(loginNote(tokenRow(b)), b).toBe(NEVER_STORE_PASSWORD);
    expect(loginNote(tokenRow("dhan"))).toMatch(/access token you stored/);
    expect(loginNote(tokenRow("coindcx"))).toMatch(/no daily login/);
  });
});

describe("UI2-0 'Log in to <Broker>' calls the broker's existing login path", () => {
  function deps(over: Partial<LoginDeps> = {}) {
    const tab = { opener: {} as unknown, location: { href: "about:blank" }, close: vi.fn() };
    const d = {
      upstoxOAuthStart: vi.fn(async () => ({ authorization_url: "https://upstox.example/login" })),
      brokerLoginUrl: vi.fn(async () => ({ authorization_url: "https://kite.example/connect" })),
      authenticateBroker: vi.fn(async () => ({})),
      openTab: vi.fn(() => tab as unknown as Window),
      redirect: vi.fn(),
      ...over,
    };
    return { d, tab };
  }

  it("Upstox: OAuth start for that account, then this tab goes to Upstox", async () => {
    const { d } = deps();
    await expect(startBrokerLogin(tokenRow("upstox", { account_label: "second" }), d)).resolves.toEqual({ kind: "redirected" });
    expect(d.upstoxOAuthStart).toHaveBeenCalledWith("second");
    expect(d.redirect).toHaveBeenCalledWith("https://upstox.example/login");
    expect(d.authenticateBroker).not.toHaveBeenCalled();
    expect(d.openTab).not.toHaveBeenCalled();
  });

  it("Zerodha and Fyers: a tab opened inside the click, pointed at the broker's login link; the code is pasted next", async () => {
    for (const b of ["zerodha", "fyers"]) {
      const { d, tab } = deps();
      const pending = startBrokerLogin(tokenRow(b), d);
      expect(d.openTab, b).toHaveBeenCalledTimes(1);               // synchronously, before any await
      await expect(pending).resolves.toEqual({ kind: "code-needed", link: "https://kite.example/connect", opened: true });
      expect(d.brokerLoginUrl).toHaveBeenCalledWith(b, "primary");
      expect(tab.location.href).toBe("https://kite.example/connect");
      expect(tab.opener).toBeNull();
      expect(d.upstoxOAuthStart).not.toHaveBeenCalled();
      expect(d.authenticateBroker).not.toHaveBeenCalled();
    }
  });

  it("login link with the tab blocked: the link is still offered", async () => {
    const { d } = deps({ openTab: vi.fn(() => null) });
    await expect(startBrokerLogin(tokenRow("zerodha"), d)).resolves.toEqual({ kind: "code-needed", link: "https://kite.example/connect", opened: false });
  });

  it("login link failing: the blank tab is closed and the error reaches the caller", async () => {
    const { d, tab } = deps({ brokerLoginUrl: vi.fn(async () => { throw new ApiError(404, "Not found."); }) });
    await expect(startBrokerLogin(tokenRow("fyers"), d)).rejects.toBeInstanceOf(ApiError);
    expect(tab.close).toHaveBeenCalled();
  });

  it("Angel One, Shoonya, Dhan, CoinDCX: authenticate with the stored keys", async () => {
    for (const b of ["angel_one", "shoonya", "dhan", "coindcx"]) {
      const { d } = deps();
      await expect(startBrokerLogin(tokenRow(b, { account_label: "desk" }), d)).resolves.toEqual({ kind: "logged-in" });
      expect(d.authenticateBroker, b).toHaveBeenCalledWith(b, "desk");
      expect(d.upstoxOAuthStart).not.toHaveBeenCalled();
      expect(d.brokerLoginUrl).not.toHaveBeenCalled();
      expect(d.openTab).not.toHaveBeenCalled();
    }
  });

  it("explains a failure as what happened and what to do next, never raw error text", () => {
    expect(loginErrorText(new ApiError(502, "x", "Broker authentication failed: Traceback..."), "angel_one"))
      .toBe("Angel One did not accept the login. Try again; if it repeats, check your Angel One keys on the Broker page.");
    expect(loginErrorText(new ApiError(404, "x"), "upstox")).toBe("Your Upstox keys are no longer stored. Connect Upstox again on the Broker page.");
    expect(loginErrorText(new ApiError(0, NETWORK_MESSAGE), "upstox")).toBe(NETWORK_MESSAGE);
    expect(loginErrorText(new TypeError("Failed to fetch"), "upstox")).toBe(NETWORK_MESSAGE);
    expect(loginErrorText(new Error("boom: stack"), "dhan")).toBe("The Dhan login did not finish. Try again in a moment.");
  });
});

describe("UI2-0 review fixes: login errors never show server text (L5)", () => {
  const RAW = "Broker authentication failed: HTTPSConnectionPool(host='api.kite.trade') token_status registry adapter";
  it("maps every status to a fixed plain-English sentence", () => {
    const cases: [number, string][] = [
      [400, "Your Zerodha keys are incomplete. Check them on the Broker page, then try again."],
      [401, "Your sign-in has ended. Sign in again, then log in to Zerodha."],
      [403, "You do not have permission to log in to Zerodha. Ask the owner of your organisation."],
      [404, "Your Zerodha keys are no longer stored. Connect Zerodha again on the Broker page."],
      [409, "Your Zerodha connection changed while you were logging in. Try again."],
      [422, "The Zerodha login did not finish. Try again in a moment."],
      [429, "Too many attempts. Wait a minute, then try again."],
      [500, "The Zerodha login did not finish. Try again in a moment."],
      [502, "Zerodha did not accept the login. Try again; if it repeats, check your Zerodha keys on the Broker page."],
      [503, "The Zerodha login did not finish. Try again in a moment."],
    ];
    for (const [status, text] of cases) {
      const shown = loginErrorText(new ApiError(status, RAW, RAW, JSON.stringify({ detail: RAW })), "zerodha");
      expect(shown, String(status)).toBe(text);
      expect(shown).not.toMatch(/HTTPS|token_status|registry|adapter|kite\.trade/);
    }
  });

  it("a 403 asking for a fresh two-factor check says so, in plain words", () => {
    const e = new ApiError(403, "x", "Logging in to the broker requires a fresh two-factor check on this session");
    expect(loginErrorText(e, "upstox")).toBe("Logging in to Upstox needs a fresh two-factor check. Confirm it with your authenticator app, then try again.");
  });
});

describe("UI2-0 review fixes: a background re-check keeps the last good answer (L4)", () => {
  it("first load fails closed at once", () => {
    expect(brokerCheckFailed({ hasGoodAnswer: false, consecutiveFailures: 1, ageMs: 0 })).toBe(true);
  });
  it("one failed re-check with a recent good answer keeps that answer", () => {
    expect(brokerCheckFailed({ hasGoodAnswer: true, consecutiveFailures: 1, ageMs: 60_000 })).toBe(false);
  });
  it("two failures in a row, or a good answer older than five minutes, says the check failed", () => {
    expect(FAILURES_BEFORE_UNAVAILABLE).toBe(2);
    expect(STALE_AFTER_MS).toBe(5 * 60 * 1000);
    expect(brokerCheckFailed({ hasGoodAnswer: true, consecutiveFailures: 2, ageMs: 1_000 })).toBe(true);
    expect(brokerCheckFailed({ hasGoodAnswer: true, consecutiveFailures: 1, ageMs: STALE_AFTER_MS + 1 })).toBe(true);
  });
  it("no failure is never 'failed', however old the answer", () => {
    expect(brokerCheckFailed({ hasGoodAnswer: true, consecutiveFailures: 0, ageMs: 10 * STALE_AFTER_MS })).toBe(false);
    expect(brokerCheckFailed({ hasGoodAnswer: false, consecutiveFailures: 0, ageMs: 0 })).toBe(false);
  });
});

describe("UI2-0 review fixes: broker answers are keyed by user (L3)", () => {
  it("another user's brokers are never shown", () => {
    const known = { userId: 1, rows: [tokenRow("upstox", { needs_login: false })], at: 1, failures: 0 };
    expect(knownFor(known, 1)).toBe(known);
    expect(knownFor(known, 2).rows).toBeNull();
    expect(knownFor(known, null).rows).toBeNull();
    expect(brokerGateState({ ...base, tokens: knownFor(known, 2).rows }).kind).toBe("loading");
  });
});
