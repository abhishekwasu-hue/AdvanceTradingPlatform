import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import appSource from "../App.tsx?raw";
import adminSource from "../pages/AdminPage.tsx?raw";
import { FeaturesProvider, snapshotFor, useFeatures, useUiV2 } from "./useFeatures";
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
    const snap = { key: 1, features: { ui_v2: true }, loaded: true };
    expect(snapshotFor(snap, true, 1)).toBe(snap);
    expect(snapshotFor(snap, true, 2)).toEqual({ key: null, features: {}, loaded: false });
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

  it("M2: the shell re-reads on route and user changes, and the Admin console after every flag change", () => {
    expect(appSource).toMatch(/function FeaturesFreshness\(\)/);
    expect(appSource).toMatch(/\}, \[pathname, user, refresh\]\);/);
    expect(appSource).toContain("<FeaturesFreshness />");
    expect(adminSource.match(/api\.adminSetFlag\([^\n]*\.then\(flagsChanged\)/g)?.length).toBe(3);
    expect(adminSource).not.toMatch(/api\.adminSetFlag\([^\n]*\.then\(setFlags\)/);
    expect(adminSource).toMatch(/const flagsChanged = \(next: FeatureFlags\) => \{ setFlags\(next\); refreshFeatures\(\); \};/);
  });

  it("M2: the provider re-reads when the tab is shown again", () => {
    expect(featuresSource).toMatch(/addEventListener\("visibilitychange", onShow\)/);
  });
});
