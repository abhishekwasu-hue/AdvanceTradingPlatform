import { afterEach, describe, expect, it, vi } from "vitest";
import { appPlatform, detectNative, isNativeApp, setNativeRuntime, type NativeRuntime } from "./platform";

/** Mobile B1: platform detection is a no-op on the web build (`__ATP_NATIVE__` is false in the tests, as on the web). */
const android: NativeRuntime = { isNativePlatform: () => true, getPlatform: () => "android" };

afterEach(() => setNativeRuntime(null));

describe("native platform", () => {
  it("the web build is never the app, even if something claims to be Capacitor", () => {
    expect(__ATP_NATIVE__).toBe(false);
    expect(isNativeApp()).toBe(false);
    setNativeRuntime(android);
    expect(isNativeApp()).toBe(false);
    expect(appPlatform()).toBe("web");
  });

  it("detects the app only in a native build whose runtime says so; failures read as web", () => {
    expect(detectNative(true, android)).toBe(true);
    expect(detectNative(false, android)).toBe(false);
    expect(detectNative(true, null)).toBe(false);
    expect(detectNative(true, { isNativePlatform: () => false, getPlatform: () => "web" })).toBe(false);
    const broken = { isNativePlatform: vi.fn(() => { throw new Error("no bridge"); }), getPlatform: () => "android" };
    expect(detectNative(true, broken)).toBe(false);
  });
});
