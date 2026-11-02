import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { BrokerTokenInfo } from "../types";
import { UpstoxSavedView, savedAnalytics } from "./ConnectPanel";

const NOW = new Date("2026-11-02T03:30:00Z");
const row = (over: Partial<BrokerTokenInfo> = {}): BrokerTokenInfo => ({
  broker_name: "upstox", account_label: "primary", token_status: "EXPIRED", token_expires_at: null, last_verified_at: null,
  needs_login: true, oauth_supported: true, oauth_callback_url: null,
  analytics_token_set: true, analytics_token_expires_at: "2027-10-20T18:30:00Z", analytics_token_expired: false, ...over,
});

describe("M85 U5: Upstox renew - the analytics token is asked once", () => {
  it("a valid saved token is found for its own account only", () => {
    expect(savedAnalytics([row()], "upstox", "primary", NOW)).toEqual({ expiresAt: "2027-10-20T18:30:00Z" });
    expect(savedAnalytics([row({ account_label: "second" })], "upstox", "primary", NOW)).toBeNull();
    expect(savedAnalytics([row()], "dhan", "primary", NOW)).toBeNull();
  });

  it("an expired, missing or unset token brings the token field back", () => {
    expect(savedAnalytics([row({ analytics_token_expired: true })], "upstox", "primary", NOW)).toBeNull();
    expect(savedAnalytics([row({ analytics_token_expires_at: "2026-11-01T00:00:00Z" })], "upstox", "primary", NOW)).toBeNull();
    expect(savedAnalytics([row({ analytics_token_set: false })], "upstox", "primary", NOW)).toBeNull();
    expect(savedAnalytics(null, "upstox", "primary", NOW)).toBeNull();
  });

  it("the saved view shows the date, the daily login button and Replace - no token field", () => {
    const html = renderToStaticMarkup(
      <UpstoxSavedView expiresAt="2027-10-20T18:30:00Z" onLogin={() => undefined} loginBusy={false} loginOpened={null} onReplace={() => undefined} />,
    );
    expect(html).toContain("Analytics token saved, valid till");
    expect(html).toMatch(/2027/);
    expect(html).toContain("data-upstox-login");
    expect(html).toContain("data-upstox-replace");
    expect(html).not.toContain("<input");
  });
});
