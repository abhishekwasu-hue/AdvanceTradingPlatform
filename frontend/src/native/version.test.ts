import { describe, expect, it, vi } from "vitest";

const getInfo = vi.fn(async () => ({ version: "9.9.9", build: "99" }));
vi.mock("@capacitor/app", () => ({ App: { getInfo } }));

import { appVersions, versionLine } from "./version";

/** Mobile B1: the versions to show; on the web only the web build id, and the native plugin is never asked. */
describe("app versions", () => {
  it("on the web: the web build id only, without touching Capacitor", async () => {
    const v = await appVersions();
    expect(v.native).toBeNull();
    expect(typeof v.web).toBe("string");
    expect(v.web.length).toBeGreaterThan(0);
    expect(getInfo).not.toHaveBeenCalled();
  });

  it("formats one line for About and bug reports", () => {
    expect(versionLine({ web: "0.1.0+abc1234", native: null })).toBe("0.1.0+abc1234");
    expect(versionLine({ web: "0.1.0+abc1234", native: { version: "0.1.0+abc1234", build: "12" } }))
      .toBe("0.1.0+abc1234 (app 0.1.0+abc1234, build 12)");
  });
});
