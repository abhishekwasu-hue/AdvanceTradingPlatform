import { afterEach, describe, expect, it, vi } from "vitest";
import { appVersion, detectPlatform, isUpdate, sendPwaEvent, startPwaEvents, type PwaEvent } from "./events";

afterEach(() => vi.unstubAllGlobals());

function memoryStorage(initial: Record<string, string> = {}): Storage {
  const data = new Map(Object.entries(initial));
  return {
    get length() { return data.size; },
    clear: () => data.clear(),
    getItem: (k: string) => data.get(k) ?? null,
    key: (i: number) => [...data.keys()][i] ?? null,
    removeItem: (k: string) => { data.delete(k); },
    setItem: (k: string, v: string) => { data.set(k, String(v)); },
  };
}

/** A window with just what startPwaEvents reads: storage, matchMedia, navigator and event listeners. */
function fakeWindow({ standalone = false, local = memoryStorage(), session = memoryStorage() } = {}) {
  const target = new EventTarget();
  const win = {
    localStorage: local,
    sessionStorage: session,
    navigator: {},
    matchMedia: (q: string) => ({ matches: standalone && q === "(display-mode: standalone)" }),
    addEventListener: target.addEventListener.bind(target),
    removeEventListener: target.removeEventListener.bind(target),
    dispatchEvent: target.dispatchEvent.bind(target),
  };
  return win as unknown as Window;
}

describe("platform family", () => {
  it("maps a User-Agent to android / ios / desktop / other", () => {
    expect(detectPlatform("Mozilla/5.0 (Linux; Android 14; Pixel 8) Chrome/129")).toBe("android");
    expect(detectPlatform("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X)")).toBe("ios");
    expect(detectPlatform("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605", 5)).toBe("ios");   // iPadOS
    expect(detectPlatform("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Chrome/129", 0)).toBe("desktop");
    expect(detectPlatform("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("desktop");
    expect(detectPlatform("")).toBe("other");
  });
});

describe("events", () => {
  it("an update is a change from a known earlier version", () => {
    expect(isUpdate(null, "0.1.0")).toBe(false);
    expect(isUpdate("0.1.0", "0.1.0")).toBe(false);
    expect(isUpdate("0.1.0", "0.2.0")).toBe(true);
    expect(typeof appVersion()).toBe("string");
  });

  it("posts only the event and the platform, and swallows failures", async () => {
    const fetchMock = vi.fn().mockRejectedValue(new TypeError("offline"));
    vi.stubGlobal("fetch", fetchMock);
    expect(() => sendPwaEvent("installed", "android")).not.toThrow();
    await Promise.resolve();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/pwa/events");
    expect(JSON.parse(init.body)).toEqual({ event: "installed", platform: "android" });
    expect(init.credentials).toBe("omit");
  });

  it("a browser tab reports nothing on load; the installed app reports its launch once per session", () => {
    const sent: PwaEvent[] = [];
    startPwaEvents(fakeWindow(), (e) => sent.push(e));
    expect(sent).toEqual([]);

    const session = memoryStorage();
    startPwaEvents(fakeWindow({ standalone: true, session }), (e) => sent.push(e));
    startPwaEvents(fakeWindow({ standalone: true, session }), (e) => sent.push(e));   // a reload in the same session
    expect(sent).toEqual(["launched_standalone"]);
  });

  it("the installed app reports an update when it opens a new version", () => {
    const sent: PwaEvent[] = [];
    const local = memoryStorage({ atp_pwa_last_version: "0.0.1-old" });
    startPwaEvents(fakeWindow({ standalone: true, local }), (e) => sent.push(e));
    expect(sent).toContain("update_applied");
    expect(local.getItem("atp_pwa_last_version")).toBe(appVersion());
  });

  it("reports install offers, the user's choice and the install", async () => {
    const sent: PwaEvent[] = [];
    const win = fakeWindow();
    const stop = startPwaEvents(win, (e) => sent.push(e));
    const prompt = Object.assign(new Event("beforeinstallprompt"), { userChoice: Promise.resolve({ outcome: "accepted" }) });
    win.dispatchEvent(prompt);
    win.dispatchEvent(Object.assign(new Event("beforeinstallprompt"), { userChoice: Promise.resolve({ outcome: "dismissed" }) }));
    await new Promise((r) => setTimeout(r, 0));
    win.dispatchEvent(new Event("appinstalled"));
    expect(sent).toEqual(["install_prompt_shown", "install_prompt_accepted", "install_prompt_dismissed", "installed"]);
    stop();
    win.dispatchEvent(new Event("appinstalled"));
    expect(sent.filter((e) => e === "installed")).toHaveLength(1);
  });
});
