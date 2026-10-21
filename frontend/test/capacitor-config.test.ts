import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/** Mobile B1: frontend/capacitor.config.ts (MOBILE_APP_PLAN §3.2, ADR-0028 decision 10) and the Vite build modes.
 * Each case loads the config fresh, with its own environment and an empty working directory (no dist/ marker), so
 * a local build lying around cannot change the result. */
let cwd = "";

beforeEach(() => {
  cwd = mkdtempSync(join(tmpdir(), "atp-cap-"));
  vi.spyOn(process, "cwd").mockReturnValue(cwd);
  vi.stubEnv("ATP_ANDROID_BUILD", "");
  vi.stubEnv("VITE_API_BASE_URL", "");
  vi.stubEnv("ATP_APP_DOMAIN", "");
  vi.resetModules();
});

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
  rmSync(cwd, { recursive: true, force: true });
});

const loadConfig = async () => (await import("../capacitor.config")).default;

describe("capacitor.config.ts", () => {
  it("bundles the web files with the final MA-6 id and no live-site server.url", async () => {
    const config = await loadConfig();
    expect(config.appId).toBe("tech.aialgodesk.app");
    expect(config.appName).toBe("ATP");
    expect(config.webDir).toBe("dist");
    expect(config.server?.url).toBeUndefined();
    expect(config.server?.androidScheme).toBe("https");
    expect(config.server?.cleartext).toBeUndefined();
    expect(config.server?.allowNavigation).toBeUndefined();          // the WebView never navigates off the bundle
    expect(config.server?.hostname).toBeUndefined();                 // the app origin stays https://localhost
    expect(config.android?.allowMixedContent).toBe(false);
  });

  it("a debug build may log and be inspected - it can only reach the placeholder", async () => {
    const config = await loadConfig();
    expect(config.loggingBehavior).toBe("debug");
    expect(config.android?.webContentsDebuggingEnabled).toBe(true);
  });

  it("an internal build logs nothing and cannot be inspected", async () => {
    vi.stubEnv("ATP_ANDROID_BUILD", "internal");
    vi.stubEnv("VITE_API_BASE_URL", "https://atp.example.com");
    const config = await loadConfig();
    expect(config.loggingBehavior).toBe("none");
    expect(config.android?.webContentsDebuggingEnabled).toBe(false);
  });

  it("a release build with the final id is accepted and logs nothing", async () => {
    vi.stubEnv("ATP_ANDROID_BUILD", "release");
    const config = await loadConfig();
    expect(config.appId).toBe("tech.aialgodesk.app");
    expect(config.loggingBehavior).toBe("none");
    expect(config.android?.webContentsDebuggingEnabled).toBe(false);
  });

  it("refuses an IP or plain-http API base", async () => {
    vi.stubEnv("ATP_ANDROID_BUILD", "internal");
    vi.stubEnv("VITE_API_BASE_URL", "http://203.0.113.10");
    await expect(loadConfig()).rejects.toThrow(/IP address/);
  });

  it("refuses a dist/ built for another build kind", async () => {
    mkdirSync(join(cwd, "dist"));
    writeFileSync(join(cwd, "dist", "atp-native-build.json"),
      JSON.stringify({ kind: "internal", apiOrigin: "https://atp.example.com", appId: "tech.aialgodesk.app" }));
    await expect(loadConfig()).rejects.toThrow(/dist\/ was built for a internal build/);
  });
});

describe("vite.config.ts build modes", () => {
  type Resolved = { define: Record<string, string>; plugins: { name?: string }[] };
  const resolve = async (mode: string) => {
    const factory = (await import("../vite.config")).default as unknown as (env: { mode: string; command: string }) => Resolved;
    return factory({ mode, command: "build" });
  };
  const names = (r: Resolved) => r.plugins.flat().map((p) => p?.name);

  it("the web build is unchanged: relative API, no native code, no marker", async () => {
    vi.stubEnv("VITE_API_BASE_URL", "https://atp.example.com");     // ignored outside the Android build
    const web = await resolve("production");
    expect(web.define.__ATP_NATIVE__).toBe("false");
    expect(web.define.__ATP_API_ORIGIN__).toBe(`""`);
    expect(names(web)).not.toContain("atp-native-build-marker");
  });

  it("the Android build gets the checked API origin and writes its marker", async () => {
    const app = await resolve("android");
    expect(app.define.__ATP_NATIVE__).toBe("true");
    expect(app.define.__ATP_API_ORIGIN__).toBe(`"https://atp-not-configured.invalid"`);
    expect(names(app)).toContain("atp-native-build-marker");
  });

  it("MA-6: an internal or release Android build talks to https://aialgodesk.tech unless VITE_API_BASE_URL overrides it", async () => {
    vi.stubEnv("ATP_ANDROID_BUILD", "internal");
    expect((await resolve("android")).define.__ATP_API_ORIGIN__).toBe(`"https://aialgodesk.tech"`);
    vi.stubEnv("ATP_ANDROID_BUILD", "release");
    vi.resetModules();
    expect((await resolve("android")).define.__ATP_API_ORIGIN__).toBe(`"https://aialgodesk.tech"`);
    vi.stubEnv("VITE_API_BASE_URL", "https://staging.example.com");
    vi.resetModules();
    expect((await resolve("android")).define.__ATP_API_ORIGIN__).toBe(`"https://staging.example.com"`);
    vi.stubEnv("VITE_API_BASE_URL", "");
    vi.stubEnv("ATP_APP_DOMAIN", "staging.example.com");             // the repository variable, when set
    vi.resetModules();
    expect((await resolve("android")).define.__ATP_API_ORIGIN__).toBe(`"https://staging.example.com"`);
    // the web build ignores all of them and stays same-origin /api
    vi.resetModules();
    expect((await resolve("production")).define.__ATP_API_ORIGIN__).toBe(`""`);
  });

  it("the Android build refuses an IP address", async () => {
    vi.stubEnv("ATP_ANDROID_BUILD", "internal");
    vi.stubEnv("VITE_API_BASE_URL", "https://203.0.113.10");
    await expect(resolve("android")).rejects.toThrow(/IP address/);
  });
});
