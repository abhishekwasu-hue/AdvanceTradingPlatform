import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import appSource from "../App.tsx?raw";
import accountSource from "../pages/AccountPage.tsx?raw";
import hostSource from "./StepUpHost.tsx?raw";
import StepUpHost from "./StepUpHost";

/** A4: the admin step-up host and the session list's "Log out other devices" (no DOM in these unit tests). */
describe("StepUpHost", () => {
  it("is mounted for platform administrators only, in both shells", () => {
    expect(appSource).toContain('<StepUpHost enabled={user?.role === "SUPER_ADMIN"} />');
    expect(appSource.indexOf("<StepUpHost")).toBeLessThan(appSource.indexOf("{children}", appSource.indexOf("<StepUpHost")));
  });

  it("registers the handler while enabled, clears it and answers 'no' when it goes away", () => {
    expect(hostSource).toContain("setStepUpHandler((why)");
    expect(hostSource).toContain("setStepUpHandler(null)");
    expect(hostSource).toContain("pending.current?.resolve(false)");
    expect(hostSource).toContain("if (pending.current) return pending.current.promise");   // one question for many refusals
  });

  it("shows nothing until a change is refused", () => {
    expect(renderToStaticMarkup(<StepUpHost enabled />)).toBe("");
    expect(renderToStaticMarkup(<StepUpHost enabled={false} />)).toBe("");
  });
});

describe("the session list", () => {
  it("offers 'Log out other devices' when another device is signed in, and names devices in plain words", () => {
    expect(accountSource).toContain("sessions.some((s) => !s.current)");
    expect(accountSource).toContain("api.logoutOtherDevices()");
    expect(accountSource).toContain("Log out other devices");
    expect(accountSource).toContain("deviceLabel(s.user_agent)");
    expect(accountSource).not.toContain('"Other device"');
  });
});
