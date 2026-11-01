import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it } from "vitest";
import { FeaturesProvider } from "../ui/useFeatures";
import { createInPlaceApi, pushedIn, type NavigateFn, type RouterLocation } from "./api";
import { InPlaceLink } from "./InPlaceLink";
import { InPlaceProvider, useInPlace } from "./InPlaceProvider";
import { AI_KEY_PANEL, BROKER_PANEL, CHART_PANEL, DEFAULT_REGISTRY } from "./panels";
import providerSource from "./InPlaceProvider.tsx?raw";
import apiSource from "./api.ts?raw";
import {
  MAX_DEPTH, createRegistry, decodePanel, encodePanel, openTransition, panelValues, presentationFor, splitPath, withPanels,
  type PanelEntry,
} from "./registry";

/**
 * M75 NAV-1: the in-place host (docs/design/M75_NO_NAVIGATION.md section 4). The host's state lives in the address
 * (`?panel=` on the same path), so open / stack / close / Back / deep links are tested against a made-up browser
 * history driving the same API the provider uses; the rendering in a real browser (focus trap, Escape, the tab's
 * scroll and form values kept, phone and desktop shapes) is e2e/inplace-host-v2.e2e.ts.
 */

/** A made-up browser history: push / replace / go(-1), as react-router's navigate drives it. */
function history(start: string, state: unknown = null) {
  const at = (path: string, st: unknown): RouterLocation => {
    const u = new URL(path, "http://app.invalid");
    return { pathname: u.pathname, search: u.search, hash: u.hash, state: st };
  };
  const entries: RouterLocation[] = [at(start, state)];
  let index = 0;
  const navigate = ((to: number | string | { pathname: string; search: string; hash: string }, opts?: { replace?: boolean; state?: unknown }) => {
    if (typeof to === "number") { index = Math.max(0, Math.min(entries.length - 1, index + to)); return; }
    const next = typeof to === "string" ? at(to, null) : { ...to, state: opts?.state ?? null };
    if (opts?.replace) entries[index] = next;
    else { entries.splice(index + 1); entries.push(next); index += 1; }
  }) as NavigateFn;
  return {
    navigate,
    location: () => entries[index],
    url: () => { const l = entries[index]; return `${l.pathname}${l.search}${l.hash}`; },
    length: () => entries.length,
    back: () => navigate(-1),
  };
}

function api(h: ReturnType<typeof history>, { enabled = true, flags = {} as Record<string, boolean>, registry = DEFAULT_REGISTRY, unregistered = [] as string[] } = {}) {
  return createInPlaceApi({ enabled, on: (f) => flags[f] === true, registry, location: h.location, navigate: h.navigate,
    onUnregistered: (p) => unregistered.push(p) });
}

describe("?panel= codec", () => {
  it("writes a readable value with ':' separators and reads it back, whatever rewrites the search", () => {
    const v = encodePanel({ kind: "chart", params: { symbol: "NIFTY 50", tf: "15min" } }, CHART_PANEL);
    expect(v).toBe("chart:NIFTY 50:15min");
    expect(withPanels("?tab=chat", [v])).toBe("?tab=chat&panel=chart:NIFTY%2050:15min");
    // A page that rewrites its search with URLSearchParams (":" becomes %3A) keeps the panel.
    const rewritten = `?${new URLSearchParams(withPanels("?tab=chat", [v])).toString()}`;
    expect(panelValues(rewritten).map((x) => decodePanel(x, DEFAULT_REGISTRY))).toEqual([{ kind: "chart", params: { symbol: "NIFTY 50", tf: "15min" } }]);
  });

  it("escapes ':' and '~' inside a parameter; empty trailing parameters are left out; an unknown kind is no panel", () => {
    const v = encodePanel({ kind: "chart", params: { symbol: "A:B~C", exchange: "BSE" } }, CHART_PANEL);
    expect(v).toBe("chart:A~3AB~7EC::BSE");
    expect(decodePanel(v, DEFAULT_REGISTRY)).toEqual({ kind: "chart", params: { symbol: "A:B~C", exchange: "BSE" } });
    expect(encodePanel({ kind: "broker" }, BROKER_PANEL)).toBe("broker");
    expect(decodePanel("nope:x", DEFAULT_REGISTRY)).toBeNull();
  });

  it("keeps the other search parameters and the panel order (bottom first)", () => {
    expect(withPanels("?a=1&panel=x&b=2", ["broker", "ai-key"])).toBe("?a=1&b=2&panel=broker&panel=ai-key");
    expect(withPanels("?panel=broker", [])).toBe("");
    expect(panelValues("?panel=broker&x=1&panel=ai-key")).toEqual(["broker", "ai-key"]);
  });
});

describe("registry", () => {
  it("resolves the full routes of the first three panels", () => {
    expect(DEFAULT_REGISTRY.resolve("/charts/NIFTY%2050?tf=15min")).toEqual({ kind: "chart", params: { symbol: "NIFTY 50", tf: "15min" } });
    expect(DEFAULT_REGISTRY.resolve("/charts/sbin?exchange=bse")).toEqual({ kind: "chart", params: { symbol: "SBIN", exchange: "BSE" } });
    expect(DEFAULT_REGISTRY.resolve("/terminal?chart=RELIANCE&tf=5min&exchange=NSE")).toEqual({ kind: "chart", params: { symbol: "RELIANCE", tf: "5min", exchange: "NSE" } });
    expect(DEFAULT_REGISTRY.resolve("/broker")).toEqual({ kind: "broker", params: {} });
    expect(DEFAULT_REGISTRY.resolve("/settings/broker/")).toEqual({ kind: "broker", params: {} });
    expect(DEFAULT_REGISTRY.resolve("/settings/ai")).toEqual({ kind: "ai-key", params: {} });
  });

  it("answers null for a route with no panel, the bare Chart tab and another site", () => {
    expect(DEFAULT_REGISTRY.resolve("/positions")).toBeNull();
    expect(DEFAULT_REGISTRY.resolve("/charts")).toBeNull();
    expect(DEFAULT_REGISTRY.resolve("https://example.com/broker")).toBeNull();
    expect(splitPath("/a/b/")?.pathname).toBe("/a/b");
  });

  it("the full route of each panel resolves back to the same panel (a link's href and the fallback are its address)", () => {
    const specs: { kind: string; params: Record<string, string> }[] = [
      { kind: "chart", params: { symbol: "NIFTY 50", tf: "15min", exchange: "BSE" } }, { kind: "broker", params: {} }, { kind: "ai-key", params: {} }];
    for (const spec of specs) {
      const entry = DEFAULT_REGISTRY.get(spec.kind) as PanelEntry;
      expect(DEFAULT_REGISTRY.resolve(entry.href(spec.params))).toEqual(spec);
    }
  });

  it("refuses a malformed or duplicate kind", () => {
    expect(() => createRegistry([{ ...BROKER_PANEL, kind: "Bad Kind" }])).toThrow(/lower case/);
    expect(() => createRegistry([BROKER_PANEL, BROKER_PANEL])).toThrow(/twice/);
  });
});

describe("open / stack / close / Back", () => {
  it("open keeps the PATH; the panel is one pushed history entry; close goes back to the exact entry before", () => {
    const h = history("/copilot?tab=chat", { from: "today" });
    api(h).open("/charts/NIFTY?tf=15min");
    expect(h.location().pathname).toBe("/copilot");
    expect(h.url()).toBe("/copilot?tab=chat&panel=chart:NIFTY:15min");
    expect(h.length()).toBe(2);
    expect(h.location().state).toEqual({ from: "today", atpInPlace: 1 });   // the tab's own handed-over state is kept
    expect(api(h).stack).toEqual([{ kind: "chart", params: { symbol: "NIFTY", tf: "15min" } }]);
    api(h).close();
    expect(h.url()).toBe("/copilot?tab=chat");
    expect(h.location().state).toEqual({ from: "today" });
    expect(api(h).stack).toEqual([]);
  });

  it("the browser Back button closes the top panel (M75N-2) and Forward opens it again", () => {
    const h = history("/positions");
    api(h).open({ kind: "broker" });
    expect(api(h).stack.map((s) => s.kind)).toEqual(["broker"]);
    h.back();
    expect(h.url()).toBe("/positions");
    expect(api(h).stack).toEqual([]);
    h.navigate(1);
    expect(api(h).stack.map((s) => s.kind)).toEqual(["broker"]);
  });

  it("stacks up to three deep, a fourth replaces the top; each close pops one level", () => {
    const h = history("/signals");
    const flags = { ai_one_key: true };
    api(h, { flags }).open({ kind: "chart", params: { symbol: "NIFTY" } });
    api(h, { flags }).open({ kind: "broker" });
    api(h, { flags }).open({ kind: "ai-key" });
    expect(api(h, { flags }).stack.map((s) => s.kind)).toEqual(["chart", "broker", "ai-key"]);
    expect(MAX_DEPTH).toBe(3);
    api(h, { flags }).open({ kind: "chart", params: { symbol: "SBIN" } });
    expect(api(h, { flags }).stack.map((s) => s.params?.symbol ?? s.kind)).toEqual(["NIFTY", "broker", "SBIN"]);
    expect(h.length()).toBe(4);   // the fourth replaced the top entry
    api(h, { flags }).close();
    expect(api(h, { flags }).stack.map((s) => s.kind)).toEqual(["chart", "broker"]);
    api(h, { flags }).close();
    api(h, { flags }).close();
    expect(h.url()).toBe("/signals");
    expect(h.location().pathname).toBe("/signals");
  });

  it("opening the panel already on top does nothing (a double click is one entry)", () => {
    const h = history("/x");
    api(h).open({ kind: "broker" });
    api(h).open("/broker");
    expect(h.length()).toBe(2);
    expect(openTransition(["broker"], "broker")).toBeNull();
  });

  it("replace changes the top panel's parameters without a new history entry", () => {
    const h = history("/x");
    api(h).open({ kind: "chart", params: { symbol: "NIFTY", tf: "5min" } });
    api(h).replace({ kind: "chart", params: { symbol: "NIFTY", tf: "60min" } });
    expect(h.length()).toBe(2);
    expect(h.url()).toBe("/x?panel=chart:NIFTY:60min");
    api(h).close();
    expect(h.url()).toBe("/x");
  });
});

describe("deep links", () => {
  it("an address with ?panel= opens the panel on load; closing it rewrites the address (nothing to go back to)", () => {
    const h = history("/copilot?panel=chart:NIFTY:15min");
    expect(api(h).stack).toEqual([{ kind: "chart", params: { symbol: "NIFTY", tf: "15min" } }]);
    expect(pushedIn(h.location().state)).toBe(0);
    api(h).close();
    expect(h.url()).toBe("/copilot");
    expect(h.length()).toBe(1);
  });

  it("an unknown or switched-off panel in the address shows nothing", () => {
    expect(api(history("/x?panel=nope:1")).stack).toEqual([]);
    expect(api(history("/x?panel=ai-key")).stack).toEqual([]);                                  // ai_one_key off
    expect(api(history("/x?panel=ai-key"), { flags: { ai_one_key: true } }).stack.map((s) => s.kind)).toEqual(["ai-key"]);
    expect(api(history("/x?panel=broker"), { enabled: false }).stack).toEqual([]);              // ui_v2 off
  });

  it("the full route itself still shows the full page: no panel answers for an address without ?panel=", () => {
    expect(api(history("/charts/NIFTY?tf=15min")).stack).toEqual([]);
    expect(api(history("/broker")).stack).toEqual([]);
  });
});

describe("fallback: navigate as today", () => {
  it("an address no panel is registered for navigates there (and warns in development)", () => {
    const h = history("/x");
    const unregistered: string[] = [];
    api(h, { unregistered }).open("/positions");
    expect(h.url()).toBe("/positions");
    expect(unregistered).toEqual(["/positions"]);
    expect(api(h).handles("/positions")).toBe(false);
  });

  it("with ui_v2 off every open navigates to the full route", () => {
    const h = history("/x");
    api(h, { enabled: false }).open({ kind: "chart", params: { symbol: "NIFTY", tf: "15min" } });
    expect(h.url()).toBe("/charts/NIFTY?tf=15min");
    expect(api(h, { enabled: false }).handles("/broker")).toBe(false);
  });

  it("a facility switched off by its flag navigates (the AI key without ai_one_key)", () => {
    const h = history("/x");
    expect(api(h).handles("/settings/ai")).toBe(false);
    api(h).open("/settings/ai");
    expect(h.url()).toBe("/settings/ai");
    expect(api(h, { flags: { ai_one_key: true } }).handles("/settings/ai")).toBe(true);
  });
});

describe("presentation: phone vs desktop", () => {
  it("a side panel / drawer from 768 px (the chart wide), a bottom sheet on a phone; the AI key a modal on both", () => {
    expect(presentationFor(CHART_PANEL.presentation, false)).toEqual({ shell: "sheet", side: "right", wide: true });
    expect(presentationFor(CHART_PANEL.presentation, true)).toEqual({ shell: "sheet", side: "bottom", wide: false });
    expect(presentationFor(BROKER_PANEL.presentation, false)).toEqual({ shell: "sheet", side: "right", wide: false });
    expect(presentationFor(BROKER_PANEL.presentation, true)).toEqual({ shell: "sheet", side: "bottom", wide: false });
    expect(presentationFor(AI_KEY_PANEL.presentation, false)).toEqual({ shell: "dialog", wide: false });
    expect(presentationFor(AI_KEY_PANEL.presentation, true)).toEqual({ shell: "dialog", wide: false });
  });

  it("the host draws every layer with the existing Dialog / Sheet (focus trap, Escape, close button) and returns focus", () => {
    expect(providerSource).toMatch(/from "\.\.\/components\/primitives\/Dialog"/);
    expect(providerSource).toMatch(/onCloseAutoFocus/);
    expect(providerSource).toMatch(/el\.focus\(\{ preventScroll: true \}\)/);
    expect(providerSource).toMatch(/if \(!o && top\) close\(\)/);   // Escape on the top layer pops one level only
    expect(apiSource).not.toMatch(/window\.location|history\.pushState/);   // only through the router: the tab stays mounted
  });
});

/** The API as a component sees it, rendered on the server (no browser needed). */
function Probe() {
  const p = useInPlace();
  return <p data-enabled={String(p.enabled)}>{p.stack.map((s) => `${s.kind}(${Object.values(s.params ?? {}).join(",")})`).join(" > ")}</p>;
}
const render = (url: string, flags: Record<string, boolean>, el = <Probe />) => renderToStaticMarkup(
  <FeaturesProvider userKey="u" value={flags}>
    <MemoryRouter initialEntries={[url]}>
      <Routes><Route path="*" element={<InPlaceProvider>{el}</InPlaceProvider>} /></Routes>
    </MemoryRouter>
  </FeaturesProvider>,
);

describe("provider", () => {
  it("reads the stack from the address while ui_v2 is on", () => {
    expect(render("/copilot?panel=chart:NIFTY:15min&panel=broker", { ui_v2: true })).toContain("chart(NIFTY,15min) &gt; broker()");
    expect(render("/copilot?panel=broker", { ui_v2: false })).toContain('data-enabled="false"></p>');
  });

  it("outside the host (the classic shell) the API navigates as today", () => {
    const html = renderToStaticMarkup(<MemoryRouter><Probe /></MemoryRouter>);
    expect(html).toContain('data-enabled="false"');
  });

  it("InPlaceLink keeps the real address in href (long-press, new tab)", () => {
    expect(render("/copilot", { ui_v2: true }, <InPlaceLink to="/charts/NIFTY?tf=15min">NIFTY</InPlaceLink>))
      .toContain('href="/charts/NIFTY?tf=15min"');
  });
});
