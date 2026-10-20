import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import appSource from "../App.tsx?raw";
import adminSource from "../pages/AdminPage.tsx?raw";
import {
  FeaturesProvider, REREAD_AFTER_MS, shouldReread, snapshotAfterError, snapshotFor, useFeatures, useUiV2, v2ThemeDefaultFrom,
} from "./useFeatures";
import featuresSource from "./useFeatures.tsx?raw";

function Probe() {
  const f = useFeatures();
  return <p>{`loaded=${f.loaded} ui_v2=${f.on("ui_v2")} watchlists=${f.on("watchlists")} v2hook=${useUiV2()}`}</p>;
}

describe("UI2-0 useFeatures", () => {
  it("reads the shell's flags from the provider", () => {
    const html = renderToStaticMarkup(<FeaturesProvider userKey={1} value={{ ui_v2: true, watchlists: false }}><Probe /></FeaturesProvider>);
    expect(html).toContain("loaded=true ui_v2=true watchlists=false v2hook=true");
  });

  it("every flag reads off until the answer is in (never on by mistake), and while signed out", () => {
    const spy = vi.spyOn(api, "myFeatures").mockResolvedValue({ ui_v2: true });
    expect(renderToStaticMarkup(<FeaturesProvider userKey={1}><Probe /></FeaturesProvider>)).toContain("loaded=false ui_v2=false");
    expect(renderToStaticMarkup(<FeaturesProvider userKey={null}><Probe /></FeaturesProvider>)).toContain("loaded=false ui_v2=false");
    expect(renderToStaticMarkup(<Probe />)).toContain("loaded=false ui_v2=false");    // outside a provider
    spy.mockRestore();
  });

  it("a flag the server sends as anything but true is off", () => {
    const html = renderToStaticMarkup(
      <FeaturesProvider userKey={1} value={{ ui_v2: "yes" as unknown as boolean }}><Probe /></FeaturesProvider>,
    );
    expect(html).toContain("ui_v2=false");
  });
});

describe("UI2-0 review fixes: useFeatures (M2, L3)", () => {
  it("L3: another user's answer is never used, not even for one render", () => {
    const snap = { key: 1, features: { ui_v2: true }, loaded: true, answered: true };
    expect(snapshotFor(snap, true, 1)).toBe(snap);
    expect(snapshotFor(snap, true, 2)).toEqual({ key: null, features: {}, loaded: false, answered: false });
    expect(snapshotFor(snap, false, 1).loaded).toBe(false);
  });

  it("M2: the shared state offers refresh() to every consumer", () => {
    let seen: unknown = null;
    function Grab() { seen = useFeatures().refresh; return null; }
    renderToStaticMarkup(<FeaturesProvider userKey={1} value={{}}><Grab /></FeaturesProvider>);
    expect(typeof seen).toBe("function");
    renderToStaticMarkup(<FeaturesProvider userKey={1}><Grab /></FeaturesProvider>);
    expect(typeof seen).toBe("function");
  });

  it("M2: the shell re-reads on route changes (throttled) and same-user re-reads, and the Admin console after every flag change", () => {
    expect(appSource).toMatch(/function FeaturesFreshness\(\)/);
    expect(appSource).toMatch(/if \(user\) refreshIfStale\(\);\n  \}, \[pathname\]\);/);
    expect(appSource).toContain("<FeaturesFreshness />");
    expect(adminSource.match(/api\.adminSetFlag\([^\n]*\.then\(flagsChanged\)/g)?.length).toBe(3);
    expect(adminSource).not.toMatch(/api\.adminSetFlag\([^\n]*\.then\(setFlags\)/);
    expect(adminSource).toMatch(/const flagsChanged = \(next: FeatureFlags\) => \{ setFlags\(next\); refreshFeatures\(\); \};/);
  });

  it("M2: the provider re-reads when the tab is shown again (sharing the 30 s throttle)", () => {
    expect(featuresSource).toMatch(/addEventListener\("visibilitychange", onShow\)/);
    expect(featuresSource).toMatch(/visibilityState === "visible"\) refreshIfStale\(\)/);
  });
});

describe("UI2-0 review fixes: throttled re-reads and failed re-reads (M3b, L7, L8)", () => {
  it("M3b: a route change or a tab shown again re-reads at most once per 30 s", () => {
    expect(REREAD_AFTER_MS).toBe(30_000);
    expect(shouldReread(null, 1_000)).toBe(true);                 // never read yet
    expect(shouldReread(1_000, 1_000)).toBe(false);
    expect(shouldReread(1_000, 30_999)).toBe(false);
    expect(shouldReread(1_000, 31_000)).toBe(true);
  });

  it("M3b: admin changes stay immediate (refresh, not refreshIfStale)", () => {
    expect(adminSource).toMatch(/setFlags\(next\); refreshFeatures\(\);/);
    expect(adminSource).toMatch(/const \{ refresh: refreshFeatures \} = useFeatures\(\);/);
  });

  it("L7: a failed re-read keeps the last good flags; only a user's first read fails closed", () => {
    const good = { key: 7, features: { ui_v2: true }, loaded: true, answered: true };
    expect(snapshotAfterError(good, 7)).toBe(good);
    expect(snapshotAfterError(good, 8)).toEqual({ key: 8, features: {}, loaded: true, answered: false });
    const first = snapshotAfterError({ key: null, features: {}, loaded: false, answered: false }, 7);
    expect(first).toEqual({ key: 7, features: {}, loaded: true, answered: false });
    expect(snapshotAfterError(first, 7)).toEqual(first);
  });

  it("L7: a failed read never moves the v2 theme default (so the hint is not cleared)", () => {
    const failed = { answered: false, on: () => false };
    expect(v2ThemeDefaultFrom(failed)).toBeNull();
    expect(v2ThemeDefaultFrom({ answered: true, on: (n: string) => n === "ui_v2" })).toBe(true);
    expect(v2ThemeDefaultFrom({ answered: true, on: () => false })).toBe(false);
    expect(appSource).toMatch(/const v2 = v2ThemeDefaultFrom\(useFeatures\(\)\);/);
    expect(appSource).toMatch(/if \(v2 !== null\) setV2ThemeDefault\(v2\);/);
  });

  it("L8: a change of user id is left to the provider; only a re-read of the same user refreshes", () => {
    expect(appSource).toMatch(/if \(user && before && before !== user && before\.id === user\.id\) refresh\(\);/);
  });

  it("the fixed-value provider reports a real answer", () => {
    let answered: boolean | null = null;
    function Grab() { answered = useFeatures().answered; return null; }
    renderToStaticMarkup(<FeaturesProvider userKey={1} value={{ ui_v2: true }}><Grab /></FeaturesProvider>);
    expect(answered).toBe(true);
  });
});
