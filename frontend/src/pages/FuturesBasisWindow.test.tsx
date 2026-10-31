import { beforeEach, describe, expect, it, vi } from "vitest";

/** M63 I-7: smoke test for the futures basis window (`?basis=<underlying>`); its panel is covered in charting/futuresBasis.test.tsx. */

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

import FuturesBasisWindow from "./FuturesBasisWindow";
import source from "./FuturesBasisWindow.tsx?raw";
import { expectNoDeveloperText, expectPlainErrorSentences, renderPage, routesErrorsThroughSentence, textOf } from "./pageSmoke.fixture";

const TRADER = { id: 1, email: "trader@example.com", tenant_id: 1, role: "OWNER", scopes: [] };
const open = (query: string) => renderPage(<FuturesBasisWindow params={new URLSearchParams(query)} />);

describe("Futures basis window", () => {
  beforeEach(() => { auth.user = TRADER; auth.loading = false; });

  it("shows its heading on the first screen, before the series answers", () => {
    const html = open("basis=nifty");
    expect(textOf(html)).toBe("NIFTY futures: basis, carry and rollover");
    expectNoDeveloperText(html);
  });

  it("asks for an underlying when the link carries none", () => {
    const html = open("");
    expect(textOf(html)).toBe("No underlying given.");
    expectNoDeveloperText(html);
  });

  it("shows a loading placeholder while the sign-in is checked", () => {
    auth.loading = true;
    expect(open("basis=NIFTY")).toMatch(/Loading/);
  });

  it("asks a signed-out visitor to log in first, in words", () => {
    auth.user = null;
    expect(textOf(open("basis=NIFTY"))).toBe("Log in in the main tab first, then open the chart again.");
  });

  it("turns every caught failure into one plain sentence, never the raw error", () => {
    expectPlainErrorSentences();
    expect(routesErrorsThroughSentence(source)).toBe(true);
  });
});
