import { afterEach, describe, expect, it, vi } from "vitest";
import { request, setStepUpHandler } from "./client";

/** A4: an admin change refused for an old two-factor check asks the registered handler (StepUpHost) for the code and is
 * sent once more; without a handler, or for "enable two-factor first", the caller gets the refusal as before. */
describe("the step-up freshness retry", () => {
  afterEach(() => { vi.unstubAllGlobals(); setStepUpHandler(null); });

  const fresh = "This action needs a fresh two-factor check (the last one was more than 30 minutes ago). Enter the code from your authenticator app.";

  function replies(...answers: { status: number; detail?: string; stepUp?: boolean }[]) {
    const fetchMock = vi.fn(async () => {
      const a = answers.shift() ?? { status: 200 };
      const headers: Record<string, string> = { "content-type": "application/json" };
      if (a.stepUp) headers["x-step-up"] = "mfa";
      return new Response(JSON.stringify(a.detail ? { detail: a.detail } : { ok: true }), { status: a.status, headers });
    });
    vi.stubGlobal("fetch", fetchMock);
    return fetchMock;
  }

  it("asks once, then repeats the request", async () => {
    const fetchMock = replies({ status: 403, detail: fresh, stepUp: true }, { status: 200 });
    const handler = vi.fn(async () => true);
    setStepUpHandler(handler);
    await expect(request("/market-holidays", { method: "POST", body: "{}" })).resolves.toEqual({ ok: true });
    expect(handler).toHaveBeenCalledWith(fresh);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("gives up after one retry and when the person cancels", async () => {
    replies({ status: 403, detail: fresh, stepUp: true }, { status: 403, detail: fresh, stepUp: true });
    const handler = vi.fn(async () => true);
    setStepUpHandler(handler);
    await expect(request("/market-holidays", { method: "POST", body: "{}" })).rejects.toMatchObject({ status: 403 });
    expect(handler).toHaveBeenCalledTimes(1);
    const fetchMock = replies({ status: 403, detail: fresh, stepUp: true });
    setStepUpHandler(async () => false);
    await expect(request("/market-holidays", { method: "POST", body: "{}" })).rejects.toMatchObject({ status: 403, message: fresh });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not ask without the header, for 'enable it first', on the sign-in routes or with no handler", async () => {
    const handler = vi.fn(async () => true);
    setStepUpHandler(handler);
    replies({ status: 403, detail: "Insufficient role for this action" });
    await expect(request("/admin/overview")).rejects.toMatchObject({ status: 403 });
    replies({ status: 403, detail: "This action requires two-factor authentication - enable it from the Account tab first", stepUp: true });
    await expect(request("/admin/overview")).rejects.toMatchObject({ status: 403 });
    replies({ status: 403, detail: fresh, stepUp: true });
    await expect(request("/auth/mfa/backup-codes", { method: "POST", body: "{}" })).rejects.toMatchObject({ status: 403 });
    expect(handler).not.toHaveBeenCalled();
    setStepUpHandler(null);
    replies({ status: 403, detail: fresh, stepUp: true });
    await expect(request("/market-holidays", { method: "POST", body: "{}" })).rejects.toMatchObject({ status: 403 });
  });
});
