import { describe, expect, it } from "vitest";
import { PUSH_CATEGORIES, pushReadiness, selectedCategories, vapidKeyBytes, type PushEnv } from "./push";

const env = (over: Partial<PushEnv> = {}): PushEnv => ({ hasServiceWorker: true, hasPushManager: true, permission: "default", ios: false, standalone: false, ...over });

describe("push readiness (PW3)", () => {
  it("asks only after explaining, on a supported browser that has not decided", () => {
    expect(pushReadiness(env())).toBe("ask");
    expect(pushReadiness(env({ permission: "granted" }))).toBe("granted");
    expect(pushReadiness(env({ permission: "denied" }))).toBe("denied");
  });

  it("sends iPhone and iPad users to install the app first", () => {
    expect(pushReadiness(env({ ios: true, standalone: false, hasPushManager: false }))).toBe("ios-install-first");
    expect(pushReadiness(env({ ios: true, standalone: true }))).toBe("ask");
  });

  it("says so when the browser cannot do push", () => {
    expect(pushReadiness(env({ hasPushManager: false }))).toBe("unsupported");
    expect(pushReadiness(env({ permission: "unavailable" }))).toBe("unsupported");
  });
});

describe("push categories", () => {
  it("treats a channel without a preference as every category", () => {
    expect(selectedCategories(null).size).toBe(PUSH_CATEGORIES.length);
    expect([...selectedCategories(["proposals"])]).toEqual(["proposals"]);
  });

  it("decodes the base64url VAPID key", () => {
    expect([...vapidKeyBytes("AQID_-8")]).toEqual([1, 2, 3, 255, 239]);
  });
});
