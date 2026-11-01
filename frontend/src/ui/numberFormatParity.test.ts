import { describe, expect, it } from "vitest";

import { formatINR, formatNumber, formatSignedINR } from "./format";
import parity from "./numberFormatShared.json";

/**
 * M77 SX-1: the cases the server's formatter (backend/app/study/numfmt.py) must match too - the backend test
 * tests/test_m77_sx1_numfmt.py reads the same file, so the PDF / PNG text and the app's text cannot drift apart.
 */
type Case = { fn: "number" | "inr" | "signedInr"; v: number; decimals?: number; opts?: Record<string, boolean>; out: string };

describe("number format parity with the server", () => {
  const cases = parity.cases as Case[];
  it("has cases", () => expect(cases.length).toBeGreaterThan(40));
  it.each(cases.map((c) => [`${c.fn}(${c.v}, ${c.decimals ?? ""} ${JSON.stringify(c.opts ?? {})})`, c] as const))("%s", (_, c) => {
    const got = c.fn === "number" ? formatNumber(c.v, c.decimals ?? 0, c.opts)
      : c.fn === "inr" ? formatINR(c.v, c.opts) : formatSignedINR(c.v, c.opts);
    expect(got).toBe(c.out);
  });
});
