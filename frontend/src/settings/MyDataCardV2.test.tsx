import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import MyDataCardV2 from "./MyDataCardV2";

// M77 SX-2: saved studies are part of "Download my data" (studies.json); the line names them only with `study_export`.
describe("MyDataCardV2 - saved studies in the download", () => {
  it("names the saved studies only when the feature is on", () => {
    expect(renderToStaticMarkup(<MyDataCardV2 studies />)).toContain("A ZIP of your profile, settings, trades, journal and saved studies.");
    const off = renderToStaticMarkup(<MyDataCardV2 />);
    expect(off).toContain("A ZIP of your profile, settings, trades and journal.");
    expect(off).not.toContain("saved studies");
  });
});
