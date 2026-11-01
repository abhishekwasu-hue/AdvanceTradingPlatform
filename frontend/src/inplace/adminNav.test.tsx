import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { createInPlaceApi, handoffs, openPanels, pushedIn, type NavigateFn, type RouterLocation } from "./api";
import { DEFAULT_REGISTRY } from "./panels";

/**
 * M75 NAV-8 (docs/design/M75_NO_NAVIGATION.md, the Admin row): the user list's "Status" opens that user's support
 * status in place over the console (the PATH and the console's own section stay; closing returns to the list). With
 * NAV-8 the guard's count is 0: no click in the app navigates away from the tab any more.
 */

const SRC = join(dirname(fileURLToPath(import.meta.url)), "..");
const read = (f: string) => readFileSync(join(SRC, f), "utf8");

/** A made-up browser history driving the same API the provider uses (as in terminalChartsNav.test.tsx). */
function history(start: string) {
  const at = (path: string, st: unknown): RouterLocation => {
    const u = new URL(path, "http://app.invalid");
    return { pathname: u.pathname, search: u.search, hash: u.hash, state: st };
  };
  const entries: RouterLocation[] = [at(start, null)];
  let index = 0;
  const navigate = ((to: number | string | { pathname: string; search: string; hash: string }, opts?: { replace?: boolean; state?: unknown }) => {
    if (typeof to === "number") { index = Math.max(0, Math.min(entries.length - 1, index + to)); return; }
    const next = typeof to === "string" ? at(to, opts?.state ?? null) : { ...to, state: opts?.state ?? null };
    if (opts?.replace) entries[index] = next;
    else { entries.splice(index + 1); entries.push(next); index += 1; }
  }) as NavigateFn;
  return { navigate, location: () => entries[index], url: () => { const l = entries[index]; return `${l.pathname}${l.search}${l.hash}`; } };
}

describe("NAV-8: the admin user list's Status opens in place", () => {
  it("admin/AdminSectionsV2.tsx:203 Status", () => {
    const h = history("/admin?section=users");
    const before = h.location();
    const api = createInPlaceApi({ enabled: true, on: () => false, registry: DEFAULT_REGISTRY, location: h.location, navigate: h.navigate });
    const to = "/admin?section=support&user=12";
    expect(api.handles(to)).toBe(true);
    api.open(to);
    expect(h.url()).toBe("/admin?section=users&panel=admin-support:12");
    expect(openPanels(h.location().search, DEFAULT_REGISTRY, () => false).map((p) => p.spec)).toEqual([{ kind: "admin-support", params: { user: "12" } }]);
    expect(pushedIn(h.location().state)).toBe(1);
    expect(handoffs(h.location().state)).toEqual([]);
    api.close();
    expect(h.location()).toBe(before);
  });

  it("only an address naming a user is the panel: the Support section itself stays the console's own tab", () => {
    expect(DEFAULT_REGISTRY.resolve("/admin?section=support")).toBeNull();
    expect(DEFAULT_REGISTRY.resolve("/admin?section=support&user=abc")).toBeNull();
    expect(DEFAULT_REGISTRY.resolve(DEFAULT_REGISTRY.get("admin-support")!.href({ user: "7" }))).toEqual({ kind: "admin-support", params: { user: "7" } });
  });

  it("the site uses the host (source)", () => {
    expect(read("admin/AdminSectionsV2.tsx")).toMatch(/<InPlaceLink to=\{`\/admin\?section=support&user=\$\{u\.id\}`\}/);
  });

  it("the guard's count is 0", () => {
    expect(read("ux/noNavigationGuard.test.ts")).toMatch(/const BASELINE = 0;/);
  });
});
