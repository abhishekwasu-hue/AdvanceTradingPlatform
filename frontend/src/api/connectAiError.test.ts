import { afterEach, describe, expect, it, vi } from "vitest";
import { CONNECT_AI_EVENT } from "../aiKeys/connect";
import { request } from "./client";

/** Settings redesign A1: an AI call that answers `ai_not_connected` opens the Connect AI panel (the shell listens for
 * the event); the caller gets the server's plain sentence. Any other refusal does not. */
describe("the ai_not_connected refusal", () => {
  afterEach(() => vi.unstubAllGlobals());

  function stub(status: number, detail: unknown) {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ detail }), { status, headers: { "content-type": "application/json" } })));
  }

  it("fires the event and keeps the plain message", async () => {
    const seen = vi.fn();
    const target = new EventTarget();                      // the tests run in Node: the page's window is stubbed
    vi.stubGlobal("window", target);
    target.addEventListener(CONNECT_AI_EVENT, seen);
    stub(409, { code: "ai_not_connected", message: "Connect AI to use this. Paste one key and every AI feature works." });
    await expect(request("/screener/nl", { method: "POST", body: "{}" })).rejects.toMatchObject({
      status: 409, message: "Connect AI to use this. Paste one key and every AI feature works.",
    });
    expect(seen).toHaveBeenCalledTimes(1);
    stub(409, { code: "running", message: "This chart is being analysed already." });
    await expect(request("/charts/analyze/items/1/analyze", { method: "POST", body: "{}" })).rejects.toMatchObject({ status: 409 });
    expect(seen).toHaveBeenCalledTimes(1);
  });
});
