import { afterEach, beforeEach, describe, expect, it } from "vitest";
import appShellSource from "./AppShellV2.tsx?raw";
import topBarSource from "./TopBarV2.tsx?raw";
import {
  SIDEBAR_NAMESPACE, SIDEBAR_SHORTCUT, SIDEBAR_STORAGE_KEY, isSidebarMode, isSidebarShortcut, pullSidebar, pushSidebar,
  readStoredSidebar, sidebarModeFor,
} from "./sidebarPrefs";

/** M57 U7-1 / U7-4: the sidebar's show / hide state - the default by width, the per-user copy on the server, the shortcut. */

/** A stand-in for `/api/me/prefs`: it keeps what was PUT, like the server's `shell` namespace. */
function fakeServer() {
  let stored: { sidebar: string | null } = { sidebar: null };
  const calls: { path: string; method: string; body?: string }[] = [];
  const fetcher = (async (path: string, init?: RequestInit) => {
    calls.push({ path, method: init?.method ?? "GET", body: init?.body as string | undefined });
    if (init?.method === "PUT") { stored = JSON.parse(init.body as string); return { namespace: "shell", version: 1, value: stored }; }
    return { prefs: { shell: stored }, versions: { shell: stored.sidebar ? 1 : 0 } };
  }) as unknown as typeof import("../api/client").request;
  return { fetcher, calls };
}

describe("M57: the sidebar mode", () => {
  it("never chosen: the full menu from 1024 px, the rail on a tablet; a choice wins at every width", () => {
    expect(sidebarModeFor(null, true)).toBe("expanded");
    expect(sidebarModeFor(null, false)).toBe("rail");
    expect(sidebarModeFor("rail", true)).toBe("rail");
    expect(sidebarModeFor("expanded", false)).toBe("expanded");
    expect(isSidebarMode("rail")).toBe(true);
    expect(isSidebarMode("hidden")).toBe(false);
  });

  it("the choice is saved per user on the server and read back after a reload (another device too)", async () => {
    const server = fakeServer();
    expect(await pullSidebar(server.fetcher)).toBeNull();                    // nothing chosen yet
    expect(await pushSidebar("rail", server.fetcher)).toBe(true);
    expect(server.calls[server.calls.length - 1]).toEqual({ path: `/me/prefs/${SIDEBAR_NAMESPACE}`, method: "PUT", body: '{"sidebar":"rail"}' });
    // A reload (or the phone, or another browser): the page reads the server's copy.
    expect(await pullSidebar(server.fetcher)).toBe("rail");
    expect(await pushSidebar("expanded", server.fetcher)).toBe(true);
    expect(await pullSidebar(server.fetcher)).toBe("expanded");
  });

  it("the server cannot be reached: nothing is lost (the device's copy stays) and nothing throws", async () => {
    const down = (async () => { throw new Error("offline"); }) as unknown as typeof import("../api/client").request;
    expect(await pullSidebar(down)).toBeNull();
    expect(await pushSidebar("rail", down)).toBe(false);
    const odd = (async () => ({ prefs: { shell: { sidebar: "sideways" } } })) as unknown as typeof import("../api/client").request;
    expect(await pullSidebar(odd)).toBeNull();
  });

  describe("this browser's copy (first paint)", () => {
    const store = new Map<string, string>();
    const saved = globalThis.localStorage;
    beforeEach(() => {
      store.clear();
      Object.defineProperty(globalThis, "localStorage", { configurable: true, value: {
        getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => { store.set(k, v); }, removeItem: (k: string) => { store.delete(k); },
      } });
    });
    afterEach(() => { Object.defineProperty(globalThis, "localStorage", { configurable: true, value: saved }); });

    it("reads only a known mode; storage that throws reads as not chosen", () => {
      expect(readStoredSidebar()).toBeNull();
      store.set(SIDEBAR_STORAGE_KEY, "rail");
      expect(readStoredSidebar()).toBe("rail");
      store.set(SIDEBAR_STORAGE_KEY, "nope");
      expect(readStoredSidebar()).toBeNull();
      Object.defineProperty(globalThis, "localStorage", { configurable: true, get: () => { throw new Error("blocked"); } });
      expect(readStoredSidebar()).toBeNull();
    });
  });

  it("the shortcut is `[` on its own, never while typing or with a modifier", () => {
    const at = (target: unknown, mods: Partial<KeyboardEvent> = {}) =>
      isSidebarShortcut({ key: SIDEBAR_SHORTCUT, ctrlKey: false, metaKey: false, altKey: false, target: target as EventTarget, ...mods });
    const el = (inField: boolean, editable = false) => ({ isContentEditable: editable, closest: () => (inField ? {} : null) });
    expect(SIDEBAR_SHORTCUT).toBe("[");
    expect(at(el(false))).toBe(true);
    expect(at(null)).toBe(true);
    expect(at(el(true))).toBe(false);                                         // an input, a textarea, a select
    expect(at(el(false, true))).toBe(false);                                  // contenteditable
    expect(at(el(false), { ctrlKey: true })).toBe(false);
    expect(at(el(false), { metaKey: true })).toBe(false);
    expect(at(el(false), { altKey: true })).toBe(false);
    expect(isSidebarShortcut({ key: "]", ctrlKey: false, metaKey: false, altKey: false, target: null })).toBe(false);
  });

  it("the shell wires it: the shortcut (left alone in a dialog), the per-user state, and one button with plain labels", () => {
    expect(appShellSource).toMatch(/isSidebarShortcut\(e\) \|\| otherDialogOpen\(document\)/);
    expect(appShellSource).toContain("useSidebarMode(user?.id ?? null, wideScreen)");
    expect(appShellSource).toMatch(/if \(phone\) openMenuSheet\(\);\s*else toggleSidebar\(\);/);
    expect(topBarSource).toContain('aria-label={menu.mode === "expanded" ? "Hide menu" : "Show menu"}');
    expect(topBarSource).toContain("aria-keyshortcuts={SIDEBAR_SHORTCUT}");
  });
});
