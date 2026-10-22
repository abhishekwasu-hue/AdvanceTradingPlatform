import { describe, expect, it } from "vitest";
import {
  ApiError, COMING_SOON_MESSAGE, NETWORK_MESSAGE, NOT_AVAILABLE_MESSAGE, SERVER_ERROR_MESSAGE, apiErrorFrom, friendlyError,
  readableDetail,
} from "./errors";

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
    expect(e.message).toBe(`${SERVER_ERROR_MESSAGE} Reference: abc123.`);
    expect(e.message).not.toMatch(/<html|uvicorn|Traceback/);
    const plain = await apiErrorFrom(response(500, "Internal Server Error"));
    expect(plain.message).toBe(SERVER_ERROR_MESSAGE);
    expect(plain.message).not.toMatch(/500|error \d/);
  });

  it("keeps the raw body for code that matches a machine code", async () => {
    const e = await apiErrorFrom(response(503, JSON.stringify({ detail: "The 'market_thesis' feature is currently disabled by the platform operator" })));
    expect(e.status).toBe(503);
    expect(e.body).toContain("market_thesis");
  });

  it("UX1: a switched-off feature says 'coming soon' - never the flag name, never a reference id", async () => {
    const body = JSON.stringify({ detail: "The 'market_thesis' feature is currently disabled by the platform operator" });
    const e = await apiErrorFrom(response(503, body, { "x-request-id": "rq-1", "x-feature-disabled": "market_thesis" }));
    expect(e.message).toBe(COMING_SOON_MESSAGE);
    expect(e.message).not.toMatch(/market_thesis|rq-1|Reference/);
    const headerOnly = await apiErrorFrom(response(503, "", { "x-feature-disabled": "screener_v2" }));
    expect(headerOnly.message).toBe(COMING_SOON_MESSAGE);
  });

  it("UX1: the server's bare 'Not found' never reaches the trader; a 404 with its own sentence keeps it", async () => {
    for (const bare of ["Not found", "Not Found", "not found."]) {
      expect((await apiErrorFrom(response(404, JSON.stringify({ detail: bare })))).message).toBe(NOT_AVAILABLE_MESSAGE);
    }
    expect((await apiErrorFrom(response(404, ""))).message).toBe(NOT_AVAILABLE_MESSAGE);
    expect((await apiErrorFrom(response(404, JSON.stringify({ detail: "No such proposal" })))).message).toBe("No such proposal");
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

describe("detail objects and server references", () => {
  it("keeps a detail object's reasons and adds the reference to every server error", async () => {
    const body = JSON.stringify({ detail: { message: "provider returned nothing usable", errors: ["NSE: timeout", "BSE: 404"] } });
    const err = await apiErrorFrom(new Response(body, { status: 502, headers: { "x-request-id": "req-9" } }));
    expect(err.message).toBe("provider returned nothing usable: NSE: timeout; BSE: 404. Reference: req-9.");
    const plain = await apiErrorFrom(new Response(JSON.stringify({ detail: "Broker down." }), { status: 503, headers: { "x-request-id": "r1" } }));
    expect(plain.message).toBe("Broker down. Reference: r1.");
  });
});
