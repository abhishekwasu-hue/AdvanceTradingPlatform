import { describe, expect, it, vi } from "vitest";

/** M55 C11: smoke test for the Marketplace page. */

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  const pending = () => new Promise(() => {});                       // no answer yet: the page shows its first screen
  return { ...real, api: new Proxy({}, { get: () => pending }) };
});
vi.mock("../auth/AuthContext", () => {
  const value = () => ({ user: { id: 1, email: "trader@example.com", tenant_id: 1, role: "OWNER", scopes: [] }, loading: false });
  return { useAuth: value, useOptionalAuth: value };
});

import MarketplacePage from "./MarketplacePage";
import { closedFlag } from "../shell/pageFlags";
import { ComingSoon } from "../ui/ComingSoon";
import { textOf } from "./pageSmoke.fixture";
import source from "./MarketplacePage.tsx?raw";
import { expectNoDeveloperText, expectPlainErrorSentences, renderPage, routesErrorsThroughSentence } from "./pageSmoke.fixture";

describe("Marketplace page", () => {
  it("shows its heading on the first screen", () => {
    expect(renderPage(<MarketplacePage />)).toContain("Marketplace");
  });

  it("shows no developer text", () => {
    expectNoDeveloperText(renderPage(<MarketplacePage />));
  });

  it("says a failed call in a plain sentence", () => {
    expectPlainErrorSentences();
    expect(routesErrorsThroughSentence(source)).toBe(true);
  });

  it("is closed behind the marketplace flag: Coming soon when off, the page when on", () => {
    expect(closedFlag("marketplace", { answered: true, on: () => false })).toBe("marketplace");
    expect(closedFlag("marketplace", { answered: true, on: () => true })).toBeNull();
    const closed = textOf(renderPage(<ComingSoon flag="marketplace" />));
    expect(closed).toContain("Coming soon");
    expect(closed).not.toContain("Discover strategies");
    expect(textOf(renderPage(<MarketplacePage />))).toContain("Discover strategies");
  });
});
