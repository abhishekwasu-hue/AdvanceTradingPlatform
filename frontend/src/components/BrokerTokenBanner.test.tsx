import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { tokenRow } from "../ui/brokerRows.fixture";
import { RedirectUriNote } from "./BrokerTokenBanner";

const PUBLIC = "https://atp.example.com/api/broker/upstox/oauth/callback";

describe("D2 BrokerTokenBanner: the Upstox redirect line", () => {
  it("shows the public Redirect URI when the saved one matches (or none is saved)", () => {
    const html = renderToStaticMarkup(<RedirectUriNote t={{ ...tokenRow("upstox"), oauth_callback_url: PUBLIC, redirect_uri_mismatch: false }} />);
    expect(html).toContain(PUBLIC);
    expect(html).not.toContain("data-redirect-mismatch");
  });

  it("points to Settings instead of an address the login will not use when the saved one differs", () => {
    const html = renderToStaticMarkup(<RedirectUriNote t={{ ...tokenRow("upstox"), oauth_callback_url: PUBLIC, redirect_uri_mismatch: true }} />);
    expect(html).toContain("data-redirect-mismatch");
    expect(html).toContain("The saved Redirect URI differs from this site&#x27;s address - see Settings");
    expect(html).not.toContain(PUBLIC);
    expect(html).not.toMatch(/text-down|bg-down/);                          // neutral, not an error
  });

  it("shows nothing for a broker without the OAuth redirect", () => {
    expect(renderToStaticMarkup(<RedirectUriNote t={tokenRow("zerodha")} />)).toBe("");
  });
});
