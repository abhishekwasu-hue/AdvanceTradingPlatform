import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import type { UpstoxRedirectInfo } from "../types";
import { copyRedirectUrl, mismatchedAccounts, showRedirectBlock, UpstoxRedirectUrlView, type CopyState } from "./UpstoxRedirectUrl";

const URL_ = "https://atp.example.com/api/broker/upstox/oauth/callback";
const clean: UpstoxRedirectInfo = { redirect_url: URL_, redirect_uri_mismatch: false, accounts: [] };
const mismatch: UpstoxRedirectInfo = {
  redirect_url: URL_, redirect_uri_mismatch: true,
  accounts: [
    { account_label: "primary", redirect_uri_set: true, redirect_uri_mismatch: true },
    { account_label: "second", redirect_uri_set: true, redirect_uri_mismatch: false },
  ],
};
const render = (info: UpstoxRedirectInfo, copyState: CopyState = "idle") =>
  renderToStaticMarkup(<UpstoxRedirectUrlView info={info} copyState={copyState} />);

describe("D2 Upstox Redirect URL: the copy button", () => {
  it("copies with the Clipboard API and reports Copied", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    const select = vi.fn();
    expect(await copyRedirectUrl(URL_, { writeText }, select)).toBe("copied");
    expect(writeText).toHaveBeenCalledWith(URL_);
    expect(select).not.toHaveBeenCalled();
  });

  it("a refused clipboard selects the address instead, without throwing", async () => {
    const writeText = vi.fn().mockRejectedValue(new Error("NotAllowedError"));
    const select = vi.fn();
    expect(await copyRedirectUrl(URL_, { writeText }, select)).toBe("selected");
    expect(select).toHaveBeenCalledTimes(1);
  });

  it("no Clipboard API at all (plain http, an old browser): the address is selected", async () => {
    const select = vi.fn();
    expect(await copyRedirectUrl(URL_, undefined, select)).toBe("selected");
    expect(select).toHaveBeenCalledTimes(1);
  });

  it("a failing select still does not throw", async () => {
    expect(await copyRedirectUrl(URL_, undefined, () => { throw new Error("no input"); })).toBe("selected");
  });

  it("shows the label, the exact URL and a Copy button that turns into Copied", () => {
    const idle = render(clean);
    expect(idle).toContain(">Redirect URL</label>");
    expect(idle).toContain(`value="${URL_}"`);
    expect(idle).toContain(">Copy</button>");
    expect(idle).toContain("Paste this address into the Redirect URL field of your app in the Upstox developer portal.");
    const copied = render(clean, "copied");
    expect(copied).toContain(">Copied</button>");
    expect(copied).toContain("Copied to the clipboard.");
    expect(render(clean, "selected")).toContain("The address is selected");
  });
});

describe("D2 Upstox Redirect URL: a saved URI the next login replaces", () => {
  it("says the next login uses this address, without the mismatch warning", () => {
    const replaced: UpstoxRedirectInfo = {
      redirect_url: URL_, redirect_uri_mismatch: false, redirect_uri_will_be_replaced: true,
      accounts: [{ account_label: "primary", redirect_uri_set: true, redirect_uri_mismatch: false, redirect_uri_will_be_replaced: true }],
    };
    const html = render(replaced);
    expect(html).toContain("The next login uses this address.");
    expect(html).not.toContain("data-redirect-mismatch");
    expect(render(clean)).not.toContain("The next login uses this address.");
  });
});

describe("D2 Upstox Redirect URL: the mismatch warning", () => {
  it("is absent when no stored Redirect URI differs", () => {
    const html = render(clean);
    expect(html).not.toContain("data-redirect-mismatch");
    expect(html).not.toContain("differs");
  });

  it("names the accounts whose stored Redirect URI differs and says what to change", () => {
    expect(mismatchedAccounts(mismatch)).toEqual(["primary"]);
    const html = render(mismatch);
    expect(html).toContain("data-redirect-mismatch");
    expect(html).toContain("The Redirect URI saved for Upstox (account &quot;primary&quot;) differs from this address.");
    expect(html).toContain("Update the Redirect URL in your Upstox app and store the credentials here again");
    expect(html).not.toContain("second");
    const both = { ...mismatch, accounts: mismatch.accounts.map((a) => ({ ...a, redirect_uri_mismatch: true })) };
    expect(render(both)).toContain("(accounts &quot;primary&quot;, &quot;second&quot;)");
  });

  it("shows while another broker is selected when a mismatch exists, and not otherwise", () => {
    expect(showRedirectBlock(mismatch, false)).toBe(true);
    expect(showRedirectBlock(mismatch, true)).toBe(true);
    expect(showRedirectBlock(clean, true)).toBe(true);
    expect(showRedirectBlock(clean, false)).toBe(false);
    expect(showRedirectBlock(null, true)).toBe(false);
  });

  it("uses the warning tokens, not a raw colour, and gives no advice", () => {
    const html = render(mismatch);
    expect(html).toMatch(/bg-warn-tint/);
    expect(html).not.toMatch(/\b(?:text|bg|border)-(?:red|amber|yellow|orange)-\d{2,3}\b/);
    expect(html).not.toMatch(/\b(you should|buy now|sell now|guaranteed|recommend)\b/i);
  });
});
