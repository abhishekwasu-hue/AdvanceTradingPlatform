import { describe, expect, it, vi } from "vitest";
import { ApiError, NETWORK_MESSAGE } from "../api/errors";
import { tokenRow } from "./brokerRows.fixture";
import {
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
      expect(note, b).toMatch(/TOTP secret you stored, encrypted/);
    }
    // ...even if a server row claimed a hosted login for one.
    expect(loginNote(tokenRow("angel_one", { login_url_supported: true }))).not.toBe(NEVER_STORE_PASSWORD);
    expect(loginNote(tokenRow("shoonya"))).toMatch(/password and TOTP secret/);
    expect(loginNote(tokenRow("angel_one"))).toMatch(/PIN and TOTP secret/);
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
      .toBe("Angel One did not accept the login. Try again; if it repeats, check your Angel One keys in Settings.");
    expect(loginErrorText(new ApiError(404, "x"), "upstox")).toBe("Your Upstox keys are no longer stored. Connect Upstox again in Settings.");
    expect(loginErrorText(new ApiError(0, NETWORK_MESSAGE), "upstox")).toBe(NETWORK_MESSAGE);
    expect(loginErrorText(new TypeError("Failed to fetch"), "upstox")).toBe(NETWORK_MESSAGE);
    expect(loginErrorText(new Error("boom: stack"), "dhan")).toBe("The Dhan login did not finish. Try again.");
  });
});
