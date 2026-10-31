import { describe, expect, it, vi } from "vitest";

/** M55 C11: smoke test for the OptionsBuilder page. */

vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  const pending = () => new Promise(() => {});                       // no answer yet: the page shows its first screen
  return { ...real, api: new Proxy({}, { get: () => pending }) };
});
vi.mock("../auth/AuthContext", () => {
  const value = () => ({ user: { id: 1, email: "trader@example.com", tenant_id: 1, role: "OWNER", scopes: [] }, loading: false });
  return { useAuth: value, useOptionalAuth: value };
});

import OptionsBuilderPage from "./OptionsBuilderPage";
import { ApiError, NETWORK_MESSAGE } from "../api/errors";
import builderApiSource from "../optionsBuilder/api.ts?raw";
import source from "./OptionsBuilderPage.tsx?raw";
import { expectNoDeveloperText, expectPlainErrorSentences, renderPage } from "./pageSmoke.fixture";

describe("OptionsBuilder page", () => {
  it("shows its heading on the first screen", () => {
    expect(renderPage(<OptionsBuilderPage />)).toContain("Plan an options trade");
  });

  it("shows no developer text", () => {
    expectNoDeveloperText(renderPage(<OptionsBuilderPage />));
  });

  it("says a failed call in a plain sentence", () => {
    expectPlainErrorSentences();
    // a failed read shows the shared client's sentence (the builder's calls all go through it), and the off switch says so in words
    expect(builderApiSource).toContain('import { request } from "../api/client"');
    expect(new ApiError(0, NETWORK_MESSAGE).message).toBe(NETWORK_MESSAGE);
    expect(source).toContain("The Options Builder is turned off for this organisation.");
    expect(source).not.toMatch(/e\.stack|JSON\.stringify\(e/);
  });
});
