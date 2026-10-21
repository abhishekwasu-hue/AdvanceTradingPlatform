import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import {
  activityProblems, appIdProblems, bundleProblems, findApiLiterals, findIpLiterals, gradleApplicationId, gradleNamespace,
  isAllowedIp, isForbiddenAndroidFile, isPublicIp, stringsProblems,
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
    expect(matches(findIpLiterals(`host = "8.8.8.8"`))).toEqual(["8.8.8.8"]);
    expect(matches(findIpLiterals(`url: https://10.1.2.3:8443/x`))).toEqual(["10.1.2.3"]);
    expect(findIpLiterals(`placeholder="203.0.113.10" a=198.51.100.2 b=192.0.2.1 c=127.0.0.1 d=0.0.0.0`)).toEqual([]);
    expect(findIpLiterals(`gradle-8.14.3-all.zip version 1.2.3.4.5 build 300.1.1.1 androidx 1.17.0`)).toEqual([]);
    expect(isAllowedIp("203.0.113.7")).toBe(true);
    expect(isAllowedIp("8.8.8.8")).toBe(false);
  });

  it("in tests, refuses only public addresses", () => {
    expect(findIpLiterals(`"10.0.0.1"`, { strictOnly: true })).toEqual([]);
    expect(matches(findIpLiterals(`"8.8.8.8"`, { strictOnly: true }))).toEqual(["8.8.8.8"]);
    expect(findIpLiterals(`"192.168.1.1" "172.16.0.1" "100.64.0.1" "198.18.0.1" "203.0.113.10" "224.0.0.1"`, { strictOnly: true })).toEqual([]);
    expect(isPublicIp("8.8.8.8")).toBe(true);
    expect(isPublicIp("9.9.9.9")).toBe(true);
    expect(isPublicIp("172.31.0.1")).toBe(false);
  });

  it("reads the Gradle applicationId and namespace and compares them with the Capacitor appId", () => {
    expect(gradleApplicationId(`defaultConfig {\n        applicationId "tech.aialgodesk.app"\n`)).toBe("tech.aialgodesk.app");
    expect(gradleApplicationId(`applicationId = 'com.example.app'`)).toBe("com.example.app");
    expect(gradleNamespace(`android {\n    namespace = "tech.aialgodesk.app"\n`)).toBe("tech.aialgodesk.app");
    expect(appIdProblems(`applicationId "tech.aialgodesk.app"`, "tech.aialgodesk.app")).toEqual([]);
    expect(appIdProblems(`namespace = "tech.aialgodesk.app"\napplicationId "tech.aialgodesk.app"`, "tech.aialgodesk.app")).toEqual([]);
    expect(appIdProblems(`applicationId "com.other.app"`, "tech.aialgodesk.app").join()).toMatch(/differs/);
    expect(appIdProblems(`namespace = "dev.atp.unpublished"\napplicationId "tech.aialgodesk.app"`, "tech.aialgodesk.app").join())
      .toMatch(/namespace "dev\.atp\.unpublished" differs/);
    expect(appIdProblems(`android {}`, "tech.aialgodesk.app").join()).toMatch(/no applicationId/);
  });

  it("checks strings.xml and the MainActivity package against the package id (MA-6)", () => {
    const strings = (id: string) => `<string name="package_name">${id}</string>\n<string name="custom_url_scheme">${id}</string>`;
    expect(stringsProblems(strings("tech.aialgodesk.app"), "tech.aialgodesk.app")).toEqual([]);
    expect(stringsProblems(strings("dev.atp.unpublished"), "tech.aialgodesk.app").join()).toMatch(/package_name .* differs/);
    expect(stringsProblems(`<string name="app_name">ATP</string>`, "tech.aialgodesk.app").join()).toMatch(/no package_name/);
    const good = { path: "tech/aialgodesk/app/MainActivity.java", text: "package tech.aialgodesk.app;\n" };
    expect(activityProblems([good], "tech.aialgodesk.app")).toEqual([]);
    expect(activityProblems([{ ...good, text: "package dev.atp.unpublished;\n" }], "tech.aialgodesk.app").join()).toMatch(/declares package/);
    expect(activityProblems([{ path: "dev/atp/unpublished/MainActivity.java", text: "package dev.atp.unpublished;" }], "tech.aialgodesk.app").join())
      .toMatch(/must be exactly/);
    expect(activityProblems([good, { path: "dev/atp/unpublished/MainActivity.java", text: "" }], "tech.aialgodesk.app").join()).toMatch(/must be exactly/);
  });

  it("the committed Android project uses the same package id and the debug suffix", () => {
    const gradle = readFileSync(join(root, "android", "app", "build.gradle"), "utf8");
    expect(appIdProblems(gradle)).toEqual([]);
    expect(gradle).toMatch(/debug \{[^}]*applicationIdSuffix "\.debug"/);
    expect(gradle).toMatch(/apply from: 'atp-build-rules\.gradle'/);
    expect(gradle).toContain(`namespace = "tech.aialgodesk.app"`);
    expect(gradle).toContain(`applicationId "tech.aialgodesk.app"`);
    const rules = readFileSync(join(root, "android", "app", "atp-build-rules.gradle"), "utf8");
    expect(rules).toContain(`def TEMPORARY_APP_ID = "dev.atp.unpublished"`);
    const main = join(root, "android", "app", "src", "main");
    expect(stringsProblems(readFileSync(join(main, "res", "values", "strings.xml"), "utf8"))).toEqual([]);
    expect(readFileSync(join(main, "java", "tech", "aialgodesk", "app", "MainActivity.java"), "utf8")).toMatch(/^package tech\.aialgodesk\.app;/m);
    const manifest = readFileSync(join(main, "AndroidManifest.xml"), "utf8");
    expect(manifest).toContain(`android:allowBackup="false"`);
    expect(manifest).toContain(`android:usesCleartextTraffic="false"`);
    expect(manifest).toContain(`android:networkSecurityConfig="@xml/network_security_config"`);
  });

  it("D3: the network security config allows https only, with the system's certificate authorities only", () => {
    const nsc = readFileSync(join(root, "android", "app", "src", "main", "res", "xml", "network_security_config.xml"), "utf8");
    const body = nsc.replace(/<!--[\s\S]*?-->/g, "");
    expect(body).toMatch(/<base-config cleartextTrafficPermitted="false">/);
    expect(body).toMatch(/<domain includeSubdomains="false">aialgodesk\.tech<\/domain>/);
    expect(body).not.toMatch(/cleartextTrafficPermitted="true"/);
    expect(body).not.toMatch(/src="user"|<debug-overrides|<pin-set|overridePins/);
    expect([...body.matchAll(/<certificates src="([^"]+)"/g)].map((m) => m[1])).toEqual(["system", "system"]);
  });

  it("refuses secrets and machine-local files in the Android project", () => {
    for (const f of ["android/app/google-services.json", "android/upload.jks", "android/release.keystore", "android/k.p12", "android/local.properties"]) {
      expect(isForbiddenAndroidFile(f), f).toBe(true);
    }
    expect(isForbiddenAndroidFile("android/app/build.gradle")).toBe(false);
    expect(isForbiddenAndroidFile("android/gradle/wrapper/gradle-wrapper.properties")).toBe(false);
  });

  it("checks the built bundle against the build settings", () => {
    const build = { kind: "debug", appId: "tech.aialgodesk.app", apiBase: "https://atp-not-configured.invalid" };
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
    expect(run.stdout).toMatch(/debug build, package tech\.aialgodesk\.app, API https:\/\/atp-not-configured\.invalid; 0 problem/);
  });

  it("passes a release build with the final id on the production domain; fails a debug build pointed at a server", () => {
    const run = (extra: Record<string, string>) =>
      spawnSync(process.execPath, [join(root, "scripts", "check-mobile-config.mjs")], { cwd: root, env: { ...process.env, ...extra }, encoding: "utf8" });
    const release = run({ ATP_ANDROID_BUILD: "release", VITE_API_BASE_URL: "", ATP_APP_DOMAIN: "" });
    expect(release.stderr).toBe("");
    expect(release.status).toBe(0);
    expect(release.stdout).toMatch(/release build, package tech\.aialgodesk\.app, API https:\/\/aialgodesk\.tech; 0 problem/);
    const fromVariable = run({ ATP_ANDROID_BUILD: "release", VITE_API_BASE_URL: "", ATP_APP_DOMAIN: "staging.example.com" });
    expect(fromVariable.status).toBe(0);
    expect(fromVariable.stdout).toMatch(/release build, package tech\.aialgodesk\.app, API https:\/\/staging\.example\.com; 0 problem/);
    const fromIp = run({ ATP_ANDROID_BUILD: "release", VITE_API_BASE_URL: "", ATP_APP_DOMAIN: "203.0.113.10" });
    expect(fromIp.status).toBe(1);
    expect(fromIp.stderr).toMatch(/IP address/);
    const debug = run({ ATP_ANDROID_BUILD: "debug", VITE_API_BASE_URL: "https://atp.example.com" });
    expect(debug.status).toBe(1);
    expect(debug.stderr).toMatch(/must not point at a real server/);
    const ip = run({ ATP_ANDROID_BUILD: "internal", VITE_API_BASE_URL: "https://203.0.113.10" });
    expect(ip.status).toBe(1);
    expect(ip.stderr).toMatch(/IP address/);
  });

  it("checks the ATP_APP_DOMAIN variable when it is set: a host name, never an IP or a URL", () => {
    const run = (domain: string) => spawnSync(process.execPath, [join(root, "scripts", "check-mobile-config.mjs")],
      { cwd: root, env: { ...process.env, ATP_ANDROID_BUILD: "", VITE_API_BASE_URL: "", ATP_APP_DOMAIN: domain }, encoding: "utf8" });
    expect(run("atp.example.com").status).toBe(0);
    const ip = run("203.0.113.10");
    expect(ip.status).toBe(1);
    expect(ip.stderr).toMatch(/ATP_APP_DOMAIN: .*IP address/);
    const url = run("http://atp.example.com");
    expect(url.status).toBe(1);
    expect(url.stderr).toMatch(/host name only/);
  });
});
