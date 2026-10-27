// @vitest-environment node
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/** UX-4: one self-hosted product font, fixed-width digits everywhere in the v2 UI. */
describe("UX-4 fonts", () => {
  it("the page asks no font CDN; the fonts come with the app", () => {
    expect(readFileSync("index.html", "utf8")).not.toMatch(/fonts\.(googleapis|gstatic)\.com/);
    const main = readFileSync("src/main.tsx", "utf8");
    expect(main).toContain('import "@fontsource-variable/inter";');
    expect(main.indexOf("@fontsource-variable/inter")).toBeLessThan(main.indexOf("./styles/index.css"));
  });

  it("Inter first in every font stack; the v2 shell turns tabular digits on for the whole page", () => {
    expect(readFileSync("src/styles/index.css", "utf8")).toMatch(/body \{\s*font-family: "Inter Variable", "Inter"/);
    expect(readFileSync("tailwind.config.js", "utf8")).toMatch(/sans: \['"Inter Variable"', "Inter"/);
    expect(readFileSync("src/styles/index.css", "utf8")).toMatch(/html\[data-numerals="tabular"\] body \{\s*font-variant-numeric: tabular-nums;/);
    expect(readFileSync("src/shell/AppShellV2.tsx", "utf8")).toContain("useTabularRoot(true);");
  });
});
