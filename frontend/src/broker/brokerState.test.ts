import { describe, expect, it } from "vitest";
import type { BrokerConnection } from "../types";
import {
  DOT_CLASS, bannerFor, brokerDot, connectionTitle, monogram, needsAttention, promptFor, shownStatus, statusText,
} from "./brokerState";

const conn = (over: Partial<BrokerConnection> = {}): BrokerConnection => ({
  broker: "upstox", account_label: "primary", name: "Upstox", status: "connected", message: "", data_only: false, renew: true, ...over,
});
const CONNECTED = conn();
const NEEDS_LOGIN = conn({ status: "needs_login", message: "Upstox needs today's login." });
const ANALYTICS_ONLY = conn({ status: "needs_login", data_only: true, renew: false });
const NOT_WORKING = conn({ broker: "dhan", name: "Dhan", status: "not_working", renew: false, message: "Dhan did not accept the token." });

describe("P2b broker state: the four states the owner named", () => {
  it("none: the prompt asks to connect; no banner; no dot", () => {
    expect(promptFor([])).toEqual({ kind: "connect" });
    expect(bannerFor([])).toBeNull();
    expect(brokerDot([])).toBeNull();
  });

  it("needs_login: the prompt says '<Broker> needs today's login' (Renew); the Dashboard banner shows; amber dot", () => {
    expect(promptFor([NEEDS_LOGIN])).toEqual({ kind: "renew", connection: NEEDS_LOGIN });
    expect(bannerFor([NEEDS_LOGIN])).toBe(NEEDS_LOGIN);
    expect(brokerDot([NEEDS_LOGIN])).toBe("needs_login");
    expect(statusText(NEEDS_LOGIN)).toBe("Needs today's login");
  });

  it("connected: no prompt, no banner, green dot", () => {
    expect(promptFor([CONNECTED])).toBeNull();
    expect(bannerFor([CONNECTED])).toBeNull();
    expect(brokerDot([CONNECTED])).toBe("connected");
    expect(statusText(CONNECTED)).toBe("Connected");
  });

  it("analytics-only Upstox: shown as Connected - no prompt, no login-needed banner, green dot", () => {
    expect(shownStatus(ANALYTICS_ONLY)).toBe("connected");
    expect(statusText(ANALYTICS_ONLY)).toBe("Connected");
    expect(promptFor([ANALYTICS_ONLY])).toBeNull();
    expect(bannerFor([ANALYTICS_ONLY])).toBeNull();
    expect(bannerFor([{ ...ANALYTICS_ONLY, renew: true }])).toBeNull();          // data-only never asks for a login
    expect(brokerDot([ANALYTICS_ONLY])).toBe("connected");
  });

  it("not known yet (loading or a failed read): nothing at all", () => {
    expect(promptFor(null)).toBeNull();
    expect(bannerFor(null)).toBeNull();
    expect(brokerDot(null)).toBeNull();
  });
});

describe("P2b broker state: several brokers", () => {
  it("one connected broker means no sign-in prompt, but a daily login elsewhere still gets the Dashboard banner", () => {
    const zerodha = conn({ broker: "zerodha", name: "Zerodha", status: "needs_login" });
    expect(promptFor([CONNECTED, zerodha])).toBeNull();
    expect(bannerFor([CONNECTED, zerodha])).toBe(zerodha);
  });

  it("a broker that is not working and none connected: the connect prompt (nothing to renew)", () => {
    expect(promptFor([NOT_WORKING])).toEqual({ kind: "connect" });
    expect(bannerFor([NOT_WORKING])).toBeNull();
  });

  it("the dot shows the broker needing the most attention: red over amber over green", () => {
    expect(brokerDot([CONNECTED, NEEDS_LOGIN])).toBe("needs_login");
    expect(brokerDot([CONNECTED, NEEDS_LOGIN, NOT_WORKING])).toBe("not_working");
    expect(needsAttention("connected")).toBe(false);
    expect(needsAttention("needs_login")).toBe(true);
    expect(needsAttention("not_working")).toBe(true);
    expect(needsAttention(null)).toBe(false);
  });

  it("a needs-login broker without a daily renewal is not offered as Renew", () => {
    expect(bannerFor([conn({ status: "needs_login", renew: false })])).toBeNull();
    expect(promptFor([conn({ status: "needs_login", renew: false })])).toEqual({ kind: "connect" });
  });
});

describe("P2b broker state: colours and words", () => {
  it("red only for 'not working'; amber for a login; green for connected - semantic tokens", () => {
    expect(DOT_CLASS).toEqual({ connected: "bg-up", needs_login: "bg-warn", not_working: "bg-down" });
  });

  it("names a second account; tiles carry the broker's initials only", () => {
    expect(connectionTitle(conn())).toBe("Upstox");
    expect(connectionTitle(conn({ account_label: "family" }))).toBe("Upstox (family)");
    expect(monogram("Angel One")).toBe("AO");
    expect(monogram("Upstox")).toBe("U");
    expect(monogram("")).toBe("?");
    expect(monogram("Zerodha (Kite)")).toBe("Z");
    expect(monogram("Shoonya (Finvasia)")).toBe("S");
  });
});
