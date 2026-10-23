import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import accountSource from "../pages/AccountPage.tsx?raw";
import shellSource from "../shell/AppShellV2.tsx?raw";
import appSource from "../App.tsx?raw";
import { InviteFriendBodyV2 } from "./InviteFriendV2";
import { REF_STORE, codeFromSearch, forgetCode, joinedLine, rememberCode, rememberedCode } from "./referral";

/** Readiness C13 (`ui_v2` + `referrals`): the invite card, the kept code and the wiring. */

// The test environment is node: a small in-memory localStorage stands in for the browser's.
beforeEach(() => {
  const store = new Map<string, string>();
  vi.stubGlobal("window", {
    localStorage: {
      getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); },
      removeItem: (k: string) => { store.delete(k); },
    },
  });
});
afterEach(() => { vi.unstubAllGlobals(); });

describe("the invite code from an address", () => {
  it("reads a well-formed code only", () => {
    expect(codeFromSearch("?ref=abcd-2345")).toBe("ABCD2345");
    expect(codeFromSearch("?ref=ABCDEFGH&x=1")).toBe("ABCDEFGH");
    for (const bad of ["", "?ref=", "?ref=ABC", "?ref=ABCDEFG0", "?ref=ABCDEFGHI", "?other=ABCDEFGH"]) expect(codeFromSearch(bad)).toBeNull();
  });

  it("is kept until it is forgotten, and a bad stored value is ignored", () => {
    rememberCode("ABCDEFGH");
    expect(rememberedCode()).toBe("ABCDEFGH");
    forgetCode();
    expect(rememberedCode()).toBeNull();
    window.localStorage.setItem(REF_STORE, "<script>");
    expect(rememberedCode()).toBeNull();
  });

  it("blocked storage loses the code, never the page", () => {
    vi.stubGlobal("window", { localStorage: { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); }, removeItem: () => { throw new Error("blocked"); } } });
    expect(() => rememberCode("ABCDEFGH")).not.toThrow();
    expect(rememberedCode()).toBeNull();
    expect(() => forgetCode()).not.toThrow();
  });
});

describe("the card", () => {
  it("before a link: one button, the reward when the operator set one", () => {
    const html = renderToStaticMarkup(<InviteFriendBodyV2 data={{ code: null, link: null, reward_text: "You both get a free month.", joined: 0 }} onCreate={() => {}} />);
    expect(html).toContain("Create my invite link");
    expect(html).toContain("You both get a free month.");
  });

  it("with a link: the link, copy, and a count - never who joined", () => {
    const html = renderToStaticMarkup(<InviteFriendBodyV2 data={{ code: "ABCDEFGH", link: "https://aialgodesk.tech/?ref=ABCDEFGH", reward_text: null, joined: 2 }} onCreate={() => {}} />);
    expect(html).toContain('value="https://aialgodesk.tech/?ref=ABCDEFGH"');
    expect(html).toContain("Copy link");
    expect(html).toContain("2 people joined through your link.");
    expect(html).not.toContain("data-reward");
  });

  it("joined in plain words", () => {
    expect(joinedLine(0)).toBe("Nobody has joined through your link yet.");
    expect(joinedLine(1)).toBe("1 person joined through your link.");
  });
});

describe("wiring", () => {
  it("the card and the claim only while `referrals` is on; the code is kept on arrival", () => {
    expect(accountSource).toContain('const referrals = v2 && features.on("referrals");');
    expect(accountSource).toContain("{referrals && <InviteFriendV2 />}");
    expect(shellSource).toContain('{user && features.on("referrals") && <ReferralClaimHost />}');
    expect(appSource).toContain("const ref = codeFromSearch(window.location.search);");
  });
});
