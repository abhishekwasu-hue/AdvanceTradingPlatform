import { describe, expect, it } from "vitest";
import { ApiError, NETWORK_MESSAGE, apiErrorFrom, friendlyError, readableDetail } from "./errors";

function response(status: number, body: string, headers: Record<string, string> = {}) {
  return new Response(body, { status, headers });
}

describe("apiErrorFrom", () => {
  it("uses the server's detail sentence, not the status line or JSON", async () => {
    const e = await apiErrorFrom(response(400, JSON.stringify({ detail: "Capital must be at least 10,000." })));
    expect(e.message).toBe("Capital must be at least 10,000.");
    expect(String(e)).toBe("Capital must be at least 10,000.");
    expect(e.status).toBe(400);
  });

  it("turns FastAPI validation lists into one readable line", async () => {
    const body = { detail: [{ loc: ["body", "risk_pct"], msg: "Input should be less than 5" }, { loc: ["query", "symbol"], msg: "Field required" }] };
    const e = await apiErrorFrom(response(422, JSON.stringify(body)));
    expect(e.message).toBe("Please check: risk pct: Input should be less than 5; symbol: Field required.");
  });

  it("hides HTML error pages and unhandled 500s behind a sentence with the request id", async () => {
    const e = await apiErrorFrom(response(502, "<html><body>502 Bad Gateway</body></html>", { "x-request-id": "abc123" }));
    expect(e.message).toBe("The server could not complete this request (error 502). Try again in a moment. Reference: abc123.");
    expect(e.message).not.toMatch(/<html|uvicorn|Traceback/);
    const plain = await apiErrorFrom(response(500, "Internal Server Error"));
    expect(plain.message).toMatch(/^The server could not complete this request \(error 500\)/);
  });

  it("keeps the raw body for code that matches a machine code", async () => {
    const e = await apiErrorFrom(response(503, JSON.stringify({ detail: "The 'market_thesis' feature is currently disabled by the platform operator" })));
    expect(e.status).toBe(503);
    expect(e.body).toContain("market_thesis");
  });

  it("falls back to a plain sentence per status when there is no detail", async () => {
    expect((await apiErrorFrom(response(429, ""))).message).toBe("Too many requests. Wait a moment and try again.");
    expect((await apiErrorFrom(response(418, ""))).message).toBe("The request failed (error 418).");
  });
});

describe("friendlyError", () => {
  it("never shows the 'Error:' prefix or a fetch TypeError", () => {
    expect(friendlyError(new ApiError(0, NETWORK_MESSAGE))).toBe(NETWORK_MESSAGE);
    expect(friendlyError(new TypeError("Failed to fetch"))).toBe(NETWORK_MESSAGE);
    expect(friendlyError(new Error("No candles for X"))).toBe("No candles for X");
    expect(friendlyError("Error: plain")).toBe("plain");
  });

  it("readableDetail accepts nested messages and rejects non-JSON", () => {
    expect(readableDetail(JSON.stringify({ detail: { message: "Nested" } }))).toBe("Nested");
    expect(readableDetail("Internal Server Error")).toBe("");
  });
});
