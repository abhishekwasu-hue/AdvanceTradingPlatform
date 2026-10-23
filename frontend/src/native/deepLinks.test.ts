import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { ALLOWED_PATHS, HOME, KIND_SCREENS, appHosts, navigateInApp, pathFromNotification, resolveDeepLink } from "./deepLinks";

/** B8: the app's deep-link router - every notification kind opens its screen; anything else opens Home. */
const hosts = appHosts("https://aialgodesk.tech");

describe("deep links (B8)", () => {
  it("knows the app's own hosts", () => {
    expect(hosts).toEqual(["aialgodesk.tech", "www.aialgodesk.tech"]);
    expect(appHosts("https://www.example.com")).toEqual(["www.example.com", "example.com"]);
    expect(appHosts("")).toEqual([]);
  });

  it("opens each notification kind on its screen", () => {
    expect(resolveDeepLink("/signals?proposal=12", hosts)).toBe("/signals?proposal=12");                  // signal -> Signals item
    expect(resolveDeepLink("/copilot/watchtower?action=7", hosts)).toBe("/copilot/watchtower?action=7"); // approval -> Approvals
    expect(resolveDeepLink("/positions?trade=33", hosts)).toBe("/positions?trade=33");                   // exit -> Positions
    expect(resolveDeepLink("/broker", hosts)).toBe("/broker");                                           // renew -> Broker
    expect(Object.values(KIND_SCREENS).every((p) => resolveDeepLink(p, hosts) === p)).toBe(true);
    expect(resolveDeepLink("/positions", hosts)).toBe("/positions");
    expect(resolveDeepLink("/notifications?n=4", hosts)).toBe("/notifications?n=4");
  });

  it("opens App Links on the app's own domain only", () => {
    expect(resolveDeepLink("https://aialgodesk.tech/signals?proposal=5", hosts)).toBe("/signals?proposal=5");
    expect(resolveDeepLink("https://www.aialgodesk.tech/broker", hosts)).toBe("/broker");
    expect(resolveDeepLink("https://aialgodesk.tech/positions/", hosts)).toBe("/positions");
    for (const bad of [
      "https://evil.example/signals?proposal=5",
      "https://aialgodesk.tech.evil.example/broker",
      "http://aialgodesk.tech/broker",
      ["https://user", "aialgodesk.tech/broker"].join("@"),   // credentials in the link (joined: not an address)
      "https://aialgodesk.tech:8443/broker",
      "//evil.example/broker",
      "javascript:alert(1)",
      "atp://signals",
      "/broker\\@evil.example",
    ]) {
      expect(resolveDeepLink(bad, hosts), bad).toBe(HOME);
    }
  });

  it("refuses unknown pages, unknown keys and ids that are not whole numbers", () => {
    for (const bad of [
      "/admin", "/settings/secret", "/signals?proposal=abc", "/signals?proposal=-1", "/signals?proposal=0", "/signals?proposal=1.5",
      "/signals?symbol=NIFTY", "/positions?trade=3&trade=4", "/broker?next=https://evil.example", "/copilot/watchtower?action=",
    ]) {
      expect(resolveDeepLink(bad, hosts), bad).toBe(HOME);
    }
    expect(resolveDeepLink("", hosts)).toBe(HOME);
    expect(resolveDeepLink(null, hosts)).toBe(HOME);
  });

  it("reads a notification tap's data block", () => {
    expect(pathFromNotification({ path: "/signals?proposal=9", kind: "signals" }, hosts)).toBe("/signals?proposal=9");
    expect(pathFromNotification({ path: "https://evil.example/" }, hosts)).toBe(HOME);
    expect(pathFromNotification({ kind: "exits" }, hosts)).toBe(HOME);
    expect(pathFromNotification(null, hosts)).toBe(HOME);
  });

  it("navigates inside the running app without a reload", () => {
    const events: string[] = [];
    const location = { pathname: "/", search: "" };
    const fake = {
      location,
      history: { pushState: (_s: unknown, _t: string, url: string) => { const u = new URL(url, "https://app.invalid"); location.pathname = u.pathname; location.search = u.search; } },
      dispatchEvent: (e: Event) => { events.push(e.type); return true; },
    } as unknown as Window;
    navigateInApp("/positions?trade=2", fake);
    expect(location.pathname + location.search).toBe("/positions?trade=2");
    expect(events).toEqual(["popstate"]);
    navigateInApp("/positions?trade=2", fake);                // already there: nothing happens
    expect(events).toEqual(["popstate"]);
  });

  it("the Android App Links filter lists only screens the router opens, on the production domain, https only", () => {
    const manifest = readFileSync(join(process.cwd(), "android", "app", "src", "main", "AndroidManifest.xml"), "utf8").replace(/<!--[\s\S]*?-->/g, "");
    const filter = /<intent-filter android:autoVerify="true">([\s\S]*?)<\/intent-filter>/.exec(manifest)?.[1] ?? "";
    expect(filter).toContain(`android:scheme="https"`);
    expect(filter).not.toMatch(/android:scheme="(?!https")/);
    expect([...filter.matchAll(/android:host="([^"]+)"/g)].map((m) => m[1])).toEqual(["aialgodesk.tech"]);
    const paths = [...filter.matchAll(/android:path(?:Prefix)?="([^"]+)"/g)].map((m) => m[1]);
    expect(paths.length).toBeGreaterThan(0);
    for (const p of paths) expect(ALLOWED_PATHS, p).toContain(p);
    for (const screen of Object.values(KIND_SCREENS)) expect(paths, screen).toContain(screen);
  });
});
