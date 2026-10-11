import { describe, expect, it } from "vitest";
import { SseParser, nextDelay } from "./sse";

describe("SSE parser (PW4)", () => {
  it("splits events across chunk boundaries and skips heartbeats", () => {
    const p = new SseParser();
    expect(p.push("event: ready\ndata: {\"retry_ms\":2000}\n\n: ping\n\nevent: ai_ac")).toEqual([
      { event: "ready", data: "{\"retry_ms\":2000}" },
    ]);
    expect(p.push("tions\ndata: {\"changed\":true}\n\n")).toEqual([{ event: "ai_actions", data: "{\"changed\":true}" }]);
  });

  it("accepts CRLF line endings", () => {
    expect(new SseParser().push("event: notifications\r\ndata: {}\r\n\r\n")).toEqual([{ event: "notifications", data: "{}" }]);
  });

  it("backs off while the server keeps failing", () => {
    expect(nextDelay(0)).toBe(2000);
    expect(nextDelay(1)).toBe(4000);
    expect(nextDelay(10)).toBe(30_000);
  });
});
