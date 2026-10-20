import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { FeaturesProvider, useFeatures, useUiV2 } from "./useFeatures";

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
