import { afterEach, beforeEach, describe, expect, it } from "vitest";
import type { BrokerConnection } from "../types";
import { promptToShow } from "./BrokerPromptHost";
import {
  clearSignIn, dismissForThisSignIn, dismissedThisSignIn, markSignIn, resetSignInMemoryForTests, signInId, takeNewAccount,
} from "./signIn";

class MemoryStorage {
  data = new Map<string, string>();
  getItem(k: string) { return this.data.has(k) ? this.data.get(k)! : null; }
  setItem(k: string, v: string) { this.data.set(k, String(v)); }
  removeItem(k: string) { this.data.delete(k); }
}

const g = globalThis as unknown as { window?: unknown };
let storage: MemoryStorage;

beforeEach(() => {
  storage = new MemoryStorage();
  g.window = { localStorage: storage };
  resetSignInMemoryForTests();
});
afterEach(() => {
  delete g.window;
  resetSignInMemoryForTests();
});

const NONE: BrokerConnection[] = [];
const CONNECTED: BrokerConnection[] = [{ broker: "upstox", account_label: "primary", name: "Upstox", status: "connected", message: "", data_only: false, renew: true }];

describe("P2b sign-in prompt: 'Later'", () => {
  it("shows right after sign-in while no broker is connected; Later hides it for the rest of this sign-in", () => {
    markSignIn(7);
    expect(promptToShow(7, "/", NONE)).toEqual({ kind: "connect" });
    dismissForThisSignIn(7);
    expect(dismissedThisSignIn(7)).toBe(true);
    expect(promptToShow(7, "/", NONE)).toBeNull();
    expect(promptToShow(7, "/positions", NONE)).toBeNull();          // every page, the whole sign-in
  });

  it("shows again at the next sign-in, until a broker is connected", () => {
    markSignIn(7);
    dismissForThisSignIn(7);
    clearSignIn();                                                    // signed out
    markSignIn(7);                                                    // signed in again
    expect(dismissedThisSignIn(7)).toBe(false);
    expect(promptToShow(7, "/", NONE)).toEqual({ kind: "connect" });
    expect(promptToShow(7, "/", CONNECTED)).toBeNull();
  });

  it("is per user: another account on this browser still sees it", () => {
    markSignIn(7);
    dismissForThisSignIn(7);
    markSignIn(8);
    expect(promptToShow(8, "/", NONE)).toEqual({ kind: "connect" });
  });

  it("is stored in localStorage per user, keyed to the sign-in (shared by every tab, like the session cookie)", () => {
    markSignIn(7);
    dismissForThisSignIn(7);
    expect(storage.getItem("atp_broker_prompt_later:7")).toBe(signInId(7));
    expect(JSON.parse(storage.getItem("atp_sign_in")!)).toMatchObject({ userId: 7, isNew: false });
  });

  it("never for a member without the brokers:write permission (review L5)", () => {
    markSignIn(7);
    expect(promptToShow(7, "/", NONE, false)).toBeNull();
    expect(promptToShow(7, "/", NONE, true)).toEqual({ kind: "connect" });
  });

  it("never on the Broker page, signed out, or before the connections are known", () => {
    markSignIn(7);
    expect(promptToShow(7, "/broker", NONE)).toBeNull();
    expect(promptToShow(7, "/broker/", NONE)).toBeNull();
    expect(promptToShow(null, "/", NONE)).toBeNull();
    expect(promptToShow(7, "/", null)).toBeNull();
  });

  it("a new tab or a reload is the same sign-in: Later still holds there (review M1)", () => {
    markSignIn(7);
    const id = signInId(7);
    dismissForThisSignIn(7);
    resetSignInMemoryForTests();                                      // another tab: only the shared storage is left
    expect(signInId(7)).toBe(id);
    expect(promptToShow(7, "/", NONE)).toBeNull();
  });

  it("only a real sign-in makes an id: a restored session without a record reads as one fixed sign-in", () => {
    expect(storage.getItem("atp_sign_in")).toBeNull();
    expect(signInId(7)).toBe("restored:7");
    expect(storage.getItem("atp_sign_in")).toBeNull();                // nothing written by a read
    expect(promptToShow(7, "/", NONE)).toEqual({ kind: "connect" });
    dismissForThisSignIn(7);
    expect(promptToShow(7, "/", NONE)).toBeNull();                    // Later holds across tabs and reloads
    markSignIn(7);                                                    // the next real sign-in
    expect(promptToShow(7, "/", NONE)).toEqual({ kind: "connect" });
  });

  it("signing out clears the record and that user's Later", () => {
    markSignIn(7);
    dismissForThisSignIn(7);
    clearSignIn();
    expect(storage.getItem("atp_sign_in")).toBeNull();
    expect(storage.getItem("atp_broker_prompt_later:7")).toBeNull();
  });

  it("works without storage (blocked): kept in memory for this page load, no error", () => {
    g.window = { get localStorage(): Storage { throw new Error("blocked"); } };
    markSignIn(9);
    expect(promptToShow(9, "/", NONE)).toEqual({ kind: "connect" });
    dismissForThisSignIn(9);
    expect(promptToShow(9, "/", NONE)).toBeNull();
  });
});

describe("P2b a brand-new account", () => {
  it("is flagged once, so the shell opens the Broker page first", () => {
    markSignIn(11, true);
    expect(takeNewAccount(11)).toBe(true);
    expect(takeNewAccount(11)).toBe(false);
    markSignIn(12);
    expect(takeNewAccount(12)).toBe(false);
  });
});
