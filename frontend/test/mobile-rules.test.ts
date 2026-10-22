import { describe, expect, it } from "vitest";
import {
  APP_ID, PLACEHOLDER_API_ORIGIN, PRODUCTION_API_ORIGIN, TEMPORARY_APP_ID, apiBaseFromDomain, apiBaseProblems, buildKindFrom,
  defaultApiBase, isIpHost, nativeBuildProblems, normaliseApiBase, resolveNativeBuild,
  // @ts-expect-error - a plain .mjs module without type declarations (test/ is outside the app's tsconfig)
} from "../scripts/mobile-rules.mjs";

/** Mobile B1: the Android build rules (scripts/mobile-rules.mjs), shared by capacitor.config.ts, vite.config.ts, the
 * CI lint and Gradle's own copy (android/app/atp-build-rules.gradle). */
describe("mobile build rules", () => {
  it("uses the final MA-6 package id and still knows the temporary one (release builds refuse it)", () => {
    expect(APP_ID).toBe("tech.aialgodesk.app");
    expect(TEMPORARY_APP_ID).toBe("dev.atp.unpublished");
    expect(nativeBuildProblems({ kind: "release", appId: APP_ID, apiBase: PRODUCTION_API_ORIGIN })).toEqual([]);
  });

  it("MA-6: the production API origin is the domain over https, never an IP, and passes every rule", () => {
    expect(PRODUCTION_API_ORIGIN).toBe("https://aialgodesk.tech");
    expect(apiBaseProblems(PRODUCTION_API_ORIGIN)).toEqual([]);
    expect(defaultApiBase("debug")).toBe(PLACEHOLDER_API_ORIGIN);
    expect(defaultApiBase("internal")).toBe(PRODUCTION_API_ORIGIN);
    expect(defaultApiBase("release")).toBe(PRODUCTION_API_ORIGIN);
  });

  it("normalises the API base: blanks and trailing slashes go, unset is empty", () => {
    expect(normaliseApiBase(undefined)).toBe("");
    expect(normaliseApiBase("  ")).toBe("");
    expect(normaliseApiBase(" https://atp.example.com/ ")).toBe("https://atp.example.com");
    expect(normaliseApiBase("https://atp.example.com//")).toBe("https://atp.example.com");
  });

  it("accepts an https domain origin, with or without a port", () => {
    expect(apiBaseProblems("https://atp.example.com")).toEqual([]);
    expect(apiBaseProblems("https://atp.example.com:8443/")).toEqual([]);
    expect(apiBaseProblems("")).toEqual([]);
  });

  it("refuses a non-https API base", () => {
    expect(apiBaseProblems("http://atp.example.com").join()).toMatch(/must use https/);
    expect(apiBaseProblems("ws://atp.example.com").join()).toMatch(/must use https/);
    expect(apiBaseProblems("atp.example.com").join()).toMatch(/not a URL/);
  });

  it("refuses an IP address in any spelling, a public one included", () => {
    for (const base of [
      "https://203.0.113.10", "http://203.0.113.10", "https://8.8.8.8", "https://10.0.0.1", "https://[2001:db8::1]", "https://[::1]",
      "https://3232235777",          // decimal IPv4 - the URL parser turns it into 192.168.1.1
      "https://0x7f.1",              // hex shorthand - 127.0.0.1
    ]) {
      expect(apiBaseProblems(base).join(), base).toMatch(/IP address/);
    }
    expect(isIpHost("atp.example.com")).toBe(false);
    expect(isIpHost("1.2.3.example.com")).toBe(false);
  });

  it("refuses localhost (the app's own origin), credentials and paths", () => {
    expect(apiBaseProblems("https://localhost").join()).toMatch(/localhost/);
    expect(apiBaseProblems("https://api.localhost").join()).toMatch(/localhost/);
    expect(apiBaseProblems("https://user:pw@atp.example.com").join()).toMatch(/user name or password/);
    expect(apiBaseProblems("https://atp.example.com/api/v1").join()).toMatch(/origin only/);
    expect(apiBaseProblems("https://atp.example.com/?x=1").join()).toMatch(/origin only/);
  });

  it("turns ATP_APP_DOMAIN into an https origin and refuses anything but a host name", () => {
    expect(apiBaseFromDomain("")).toBe("");
    expect(apiBaseFromDomain(undefined)).toBe("");
    expect(apiBaseFromDomain(" atp.example.com. ")).toBe("https://atp.example.com");
    for (const bad of ["https://atp.example.com", "atp.example.com/api", "atp.example.com:443", "a b"]) {
      expect(() => apiBaseFromDomain(bad), bad).toThrow(/host name only/);
    }
  });

  it("knows the build kinds, debug by default", () => {
    expect(buildKindFrom({})).toBe("debug");
    expect(buildKindFrom({ ATP_ANDROID_BUILD: "release" })).toBe("release");
    expect(() => buildKindFrom({ ATP_ANDROID_BUILD: "prod" })).toThrow(/not one of debug, internal, release/);
  });

  it("refuses a release build with the temporary package id (with or without the debug suffix)", () => {
    const base = "https://atp.example.com";
    expect(nativeBuildProblems({ kind: "release", appId: TEMPORARY_APP_ID, apiBase: base }).join()).toMatch(/temporary package id/);
    expect(nativeBuildProblems({ kind: "release", appId: `${TEMPORARY_APP_ID}.debug`, apiBase: base }).join()).toMatch(/temporary package id/);
    expect(nativeBuildProblems({ kind: "release", appId: "com.example.app", apiBase: base })).toEqual([]);
    expect(() => resolveNativeBuild({ ATP_ANDROID_BUILD: "release", VITE_API_BASE_URL: base }, TEMPORARY_APP_ID))
      .toThrow(/temporary package id/);
  });

  it("keeps a debuggable build away from real servers (ADR-0028 decision 10)", () => {
    const debug = resolveNativeBuild({}, APP_ID);
    expect(debug).toEqual({ kind: "debug", appId: APP_ID, apiBase: PLACEHOLDER_API_ORIGIN, debuggable: true });
    expect(new URL(PLACEHOLDER_API_ORIGIN).hostname.endsWith(".invalid")).toBe(true);
    expect(() => resolveNativeBuild({ VITE_API_BASE_URL: "https://atp.example.com" }, APP_ID)).toThrow(/must not point at a real server/);
    expect(resolveNativeBuild({ VITE_API_BASE_URL: PLACEHOLDER_API_ORIGIN }, APP_ID).apiBase).toBe(PLACEHOLDER_API_ORIGIN);
  });

  it("internal and release builds use the production domain by default, VITE_API_BASE_URL overrides it", () => {
    expect(resolveNativeBuild({ ATP_ANDROID_BUILD: "internal" }, APP_ID))
      .toEqual({ kind: "internal", appId: APP_ID, apiBase: "https://aialgodesk.tech", debuggable: false });
    expect(resolveNativeBuild({ ATP_ANDROID_BUILD: "release", VITE_API_BASE_URL: " " }, APP_ID).apiBase).toBe("https://aialgodesk.tech");
    expect(resolveNativeBuild({ ATP_ANDROID_BUILD: "release", VITE_API_BASE_URL: "https://staging.example.com" }, APP_ID).apiBase)
      .toBe("https://staging.example.com");
    expect(() => resolveNativeBuild({ ATP_ANDROID_BUILD: "release", VITE_API_BASE_URL: "http://aialgodesk.tech" }, APP_ID))
      .toThrow(/must use https/);
  });

  it("ATP_APP_DOMAIN (the repository variable) sets the origin of internal and release builds; never of a debug build", () => {
    expect(resolveNativeBuild({ ATP_ANDROID_BUILD: "release", ATP_APP_DOMAIN: "aialgodesk.tech" }, APP_ID).apiBase).toBe("https://aialgodesk.tech");
    expect(resolveNativeBuild({ ATP_ANDROID_BUILD: "internal", ATP_APP_DOMAIN: "staging.example.com." }, APP_ID).apiBase)
      .toBe("https://staging.example.com");
    // VITE_API_BASE_URL wins over the variable
    expect(resolveNativeBuild({ ATP_ANDROID_BUILD: "release", ATP_APP_DOMAIN: "staging.example.com", VITE_API_BASE_URL: "https://aialgodesk.tech" }, APP_ID).apiBase)
      .toBe("https://aialgodesk.tech");
    // a debug build ignores it and keeps the placeholder (ADR-0028 decision 10)
    expect(resolveNativeBuild({ ATP_APP_DOMAIN: "aialgodesk.tech" }, APP_ID).apiBase).toBe(PLACEHOLDER_API_ORIGIN);
    // the same checks: never an IP, a host name only
    expect(() => resolveNativeBuild({ ATP_ANDROID_BUILD: "release", ATP_APP_DOMAIN: "203.0.113.10" }, APP_ID)).toThrow(/IP address/);
    expect(() => resolveNativeBuild({ ATP_ANDROID_BUILD: "release", ATP_APP_DOMAIN: "http://aialgodesk.tech" }, APP_ID)).toThrow(/host name only/);
  });

  it("needs a real API origin for internal and release builds, and never makes them debuggable", () => {
    expect(() => resolveNativeBuild({ ATP_ANDROID_BUILD: "internal", VITE_API_BASE_URL: PLACEHOLDER_API_ORIGIN }, APP_ID))
      .toThrow(/needs the real API origin/);
    const internal = resolveNativeBuild({ ATP_ANDROID_BUILD: "internal", VITE_API_BASE_URL: "https://atp.example.com/" }, APP_ID);
    expect(internal).toEqual({ kind: "internal", appId: APP_ID, apiBase: "https://atp.example.com", debuggable: false });
    expect(() => resolveNativeBuild({ ATP_ANDROID_BUILD: "internal", VITE_API_BASE_URL: "https://203.0.113.10" }, APP_ID))
      .toThrow(/IP address/);
  });

  it("refuses an invalid package id", () => {
    expect(nativeBuildProblems({ kind: "debug", appId: "ATP", apiBase: "" }).join()).toMatch(/not a valid Android application id/);
  });
});
