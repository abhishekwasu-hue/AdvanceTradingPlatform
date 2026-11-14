import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ApiError } from "../api/errors";
import { ConnectionRow } from "./BrokerPage";
import { needsAppKeys } from "./UpstoxOrderLogin";
import type { BrokerConnection } from "../types";

/** Owner 4 Nov: the Analytics Token cannot place orders - the Upstox row offers Upstox's daily login for orders. */
describe("Upstox login for orders", () => {
  const row = (over: Partial<BrokerConnection> = {}): BrokerConnection =>
    ({ broker: "upstox", account_label: "primary", name: "Upstox", status: "connected", message: "", data_only: true, renew: false, ...over });

  it("a data-only Upstox row shows 'Log in for orders' next to Remove", () => {
    const html = renderToStaticMarkup(<ConnectionRow c={row()} onOrderLogin={() => undefined} onRemove={() => undefined} />);
    expect(html).toContain("Log in for orders");
    expect(html).toContain("Remove");
  });

  it("owner 14 Nov: a logged-in Upstox row says 'Logged in', otherwise 'Upstox login'", () => {
    const on = renderToStaticMarkup(<ConnectionRow c={row({ data_only: false })} onOrderLogin={() => undefined} />);
    expect(on).toContain("Logged in ✓");
    expect(on).not.toContain(">Upstox login<");
    const off = renderToStaticMarkup(<ConnectionRow c={row({ data_only: false, status: "needs_login" })} onOrderLogin={() => undefined} />);
    expect(off).toContain("Upstox login");
    expect(off).not.toContain("Logged in ✓");
  });

  it("another broker's row has no Upstox login button", () => {
    const html = renderToStaticMarkup(<ConnectionRow c={row({ broker: "dhan", name: "Dhan", data_only: false })} onOrderLogin={() => undefined} />);
    expect(html).not.toContain("data-upstox-order-login-open");
  });

  it("the server's 'store the API key / secret first' answer opens the one-time key step", () => {
    const make = (status: number, detail: string) => Object.assign(Object.create(ApiError.prototype), { status, detail, body: "" }) as ApiError;
    expect(needsAppKeys(make(404, "Store your Upstox API key/secret on the Broker page first (primary)"))).toBe(true);
    expect(needsAppKeys(make(400, "Upstox credentials need both api_key and api_secret for OAuth"))).toBe(true);
    expect(needsAppKeys(make(429, "slow down"))).toBe(false);
    expect(needsAppKeys(new Error("x"))).toBe(false);
  });
});
