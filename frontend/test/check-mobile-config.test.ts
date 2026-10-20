import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  appIdProblems, bundleProblems, findApiLiterals, findIpLiterals, gradleApplicationId, isAllowedIp, isForbiddenAndroidFile,
  // @ts-expect-error - a plain .mjs script without type declarations (test/ is outside the app's tsconfig)
} from "../scripts/check-mobile-config.mjs";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const matches = (found: { match: string }[]) => found.map((f) => f.match);

/** Mobile B1: the app's configuration lint (scripts/check-mobile-config.mjs; frontend CI job and android.yml). */
describe("check-mobile-config", () => {
  it("finds quoted /api/ literals, not comments or other paths", () => {
    expect(matches(findApiLiterals(`const a = "/api/v1/x";`))).toEqual([`"/api/v1/x`]);
    expect(findApiLiterals(`fetch('/api/v1/live')`)).toHaveLength(1);
    expect(findApiLiterals("fetch(`/api/v1/${id}`)")).toHaveLength(1);
    expect(findApiLiterals(`// GET /api/v1/status\n/** \`/api/journal/*\` */\nconst b = "/apis";`)).toEqual([]);
    expect(findApiLiterals(`const url = apiUrl("/status");`)).toEqual([]);
  });

  it("finds IP literals; documentation ranges and loopback are allowed; versions are not addresses", () => {
    expect(matches(findIpLiterals(`host = "187.126.125.208"`))).toEqual(["187.126.125.208"]);
    expect(matches(findIpLiterals(`url: https://10.1.2.3:8443/x`))).toEqual(["10.1.2.3"]);
    expect(findIpLiterals(`placeholder="203.0.113.10" a=198.51.100.2 b=192.0.2.1 c=127.0.0.1 d=0.0.0.0`)).toEqual([]);
    expect(findIpLiterals(`gradle-8.14.3-all.zip version 1.2.3.4.5 build 300.1.1.1 androidx 1.17.0`)).toEqual([]);
    expect(isAllowedIp("203.0.113.7")).toBe(true);
    expect(isAllowedIp("187.126.125.208")).toBe(false);
  });

  it("in tests, refuses only the production host's range", () => {
    expect(findIpLiterals(`"10.0.0.1"`, { strictOnly: true })).toEqual([]);
    expect(matches(findIpLiterals(`"187.126.125.208"`, { strictOnly: true }))).toEqual(["187.126.125.208"]);
  });

  it("reads the Gradle applicationId and compares it with the Capacitor appId", () => {
    expect(gradleApplicationId(`defaultConfig {\n        applicationId "dev.atp.unpublished"\n`)).toBe("dev.atp.unpublished");
    expect(gradleApplicationId(`applicationId = 'com.example.app'`)).toBe("com.example.app");
    expect(appIdProblems(`applicationId "dev.atp.unpublished"`, "dev.atp.unpublished")).toEqual([]);
    expect(appIdProblems(`applicationId "com.other.app"`, "dev.atp.unpublished").join()).toMatch(/differs/);
    expect(appIdProblems(`android {}`, "dev.atp.unpublished").join()).toMatch(/no applicationId/);
  });

  it("the committed Android project uses the same package id and the debug suffix", () => {
    const gradle = readFileSync(join(root, "android", "app", "build.gradle"), "utf8");
    expect(appIdProblems(gradle)).toEqual([]);
    expect(gradle).toMatch(/debug \{[^}]*applicationIdSuffix "\.debug"/);
    expect(gradle).toMatch(/apply from: 'atp-build-rules\.gradle'/);
    const rules = readFileSync(join(root, "android", "app", "atp-build-rules.gradle"), "utf8");
    expect(rules).toContain(`def TEMPORARY_APP_ID = "dev.atp.unpublished"`);
    const manifest = readFileSync(join(root, "android", "app", "src", "main", "AndroidManifest.xml"), "utf8");
    expect(manifest).toContain(`android:allowBackup="false"`);
    expect(manifest).toContain(`android:usesCleartextTraffic="false"`);
  });

  it("refuses secrets and machine-local files in the Android project", () => {
    for (const f of ["android/app/google-services.json", "android/upload.jks", "android/release.keystore", "android/k.p12", "android/local.properties"]) {
      expect(isForbiddenAndroidFile(f), f).toBe(true);
    }
    expect(isForbiddenAndroidFile("android/app/build.gradle")).toBe(false);
    expect(isForbiddenAndroidFile("android/gradle/wrapper/gradle-wrapper.properties")).toBe(false);
  });

  it("checks the built bundle against the build settings", () => {
    const build = { kind: "debug", appId: "dev.atp.unpublished", apiBase: "https://atp-not-configured.invalid" };
    expect(bundleProblems({ ...build, apiOrigin: build.apiBase }, build)).toEqual([]);
    expect(bundleProblems(null, build).join()).toMatch(/not the app's bundle/);
    expect(bundleProblems({ kind: "internal", apiOrigin: "https://atp.example.com", appId: build.appId }, build).join()).toMatch(/was built for internal/);
  });

  it("passes on the repository as committed (no env)", () => {
    const env = { ...process.env };
    delete env.ATP_ANDROID_BUILD;
    delete env.VITE_API_BASE_URL;
    delete env.ATP_APP_DOMAIN;
    const run = spawnSync(process.execPath, [join(root, "scripts", "check-mobile-config.mjs")], { cwd: root, env, encoding: "utf8" });
    expect(run.stderr).toBe("");
    expect(run.status).toBe(0);
    expect(run.stdout).toMatch(/debug build, package dev\.atp\.unpublished, API https:\/\/atp-not-configured\.invalid; 0 problem/);
  });

  it("fails a release build with the temporary id, and a debug build pointed at a server", () => {
    const run = (extra: Record<string, string>) =>
      spawnSync(process.execPath, [join(root, "scripts", "check-mobile-config.mjs")], { cwd: root, env: { ...process.env, ...extra }, encoding: "utf8" });
    const release = run({ ATP_ANDROID_BUILD: "release", VITE_API_BASE_URL: "https://atp.example.com" });
    expect(release.status).toBe(1);
    expect(release.stderr).toMatch(/temporary package id/);
    const debug = run({ ATP_ANDROID_BUILD: "debug", VITE_API_BASE_URL: "https://atp.example.com" });
    expect(debug.status).toBe(1);
    expect(debug.stderr).toMatch(/must not point at a real server/);
    const ip = run({ ATP_ANDROID_BUILD: "internal", VITE_API_BASE_URL: "https://187.126.125.208" });
    expect(ip.status).toBe(1);
    expect(ip.stderr).toMatch(/IP address/);
  });

  it("checks the ATP_APP_DOMAIN variable when it is set: a host name, never an IP or a URL", () => {
    const run = (domain: string) => spawnSync(process.execPath, [join(root, "scripts", "check-mobile-config.mjs")],
      { cwd: root, env: { ...process.env, ATP_ANDROID_BUILD: "", VITE_API_BASE_URL: "", ATP_APP_DOMAIN: domain }, encoding: "utf8" });
    expect(run("atp.example.com").status).toBe(0);
    const ip = run("187.126.125.208");
    expect(ip.status).toBe(1);
    expect(ip.stderr).toMatch(/ATP_APP_DOMAIN: .*IP address/);
    const url = run("http://atp.example.com");
    expect(url.status).toBe(1);
    expect(url.stderr).toMatch(/host name only/);
  });
});
