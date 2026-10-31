import { beforeEach, describe, expect, it, vi } from "vitest";

/** M63 I-7: smoke test for the contract chart window (`?contract=<leg>`); its panel is covered in charting/contracts.test.tsx. */

const auth = vi.hoisted(() => ({ user: null as unknown, loading: false }));
vi.mock("../api/client", async (importOriginal) => {
  const real = await importOriginal<typeof import("../api/client")>();
  const pending = () => new Promise(() => {});                       // no answer yet: the window shows its first screen
  return { ...real, api: new Proxy({}, { get: () => pending }) };
});
vi.mock("../auth/AuthContext", () => {
  const value = () => auth;
  return { useAuth: value, useOptionalAuth: value };
});

import ContractChartWindow from "./ContractChartWindow";
import source from "./ContractChartWindow.tsx?raw";
import { expectNoDeveloperText, expectPlainErrorSentences, renderPage, routesErrorsThroughSentence, textOf } from "./pageSmoke.fixture";

const TRADER = { id: 1, email: "trader@example.com", tenant_id: 1, role: "OWNER", scopes: [] };
const open = (query: string) => renderPage(<ContractChartWindow params={new URLSearchParams(query)} />);

describe("Contract chart window", () => {
  beforeEach(() => { auth.user = TRADER; auth.loading = false; });

  it("shows the contract as its heading on the first screen, before the series answers", () => {
    const html = open("contract=NIFTY-FUT");
    expect(html).toMatch(/<h1[^>]*>NIFTY-FUT<\/h1>/);
    expectNoDeveloperText(html);
  });

  it("asks for a contract when the link carries none", () => {
    const html = open("");
    expect(textOf(html)).toBe("No contract given.");
    expectNoDeveloperText(html);
  });

  it("shows a loading placeholder while the sign-in is checked", () => {
    auth.loading = true;
    expect(open("contract=NIFTY-FUT")).toMatch(/Loading/);
  });

  it("asks a signed-out visitor to log in first, in words", () => {
    auth.user = null;
    expect(textOf(open("contract=NIFTY-FUT"))).toBe("Log in in the main tab first, then open the chart again.");
  });

  it("turns every caught failure into one plain sentence, never the raw error", () => {
    expectPlainErrorSentences();
    expect(routesErrorsThroughSentence(source)).toBe(true);
  });
});
