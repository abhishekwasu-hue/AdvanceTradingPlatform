import { describe, expect, it } from "vitest";
import { showsReferenceLabel } from "./api";
import { templateIdOf } from "./link";
import { hasHowItWorks } from "./TemplateLibrarySection";

// M97 PR-3: family I (Supply & Demand) follows `sd_pack` the way family H follows `intraday_pack`.
describe("family I (M97 PR-3)", () => {
  it("has a how-it-works screen only while sd_pack is on; family H is unchanged", () => {
    expect(hasHowItWorks({ family: "I" }, false, true)).toBe(true);
    expect(hasHowItWorks({ family: "I" }, true, false)).toBe(false);
    expect(hasHowItWorks({ family: "H" }, true)).toBe(true);
    expect(hasHowItWorks({ family: "H" }, false, true)).toBe(false);
    expect(hasHowItWorks({ family: "A" }, true, true)).toBe(false);
  });

  it("shows the reference-values label with its own flag", () => {
    expect(showsReferenceLabel({ family: "I", reference_values: true }, false, true)).toBe(true);
    expect(showsReferenceLabel({ family: "I", reference_values: true }, true, false)).toBe(false);
    expect(showsReferenceLabel({ family: "H", reference_values: true }, true)).toBe(true);
    expect(showsReferenceLabel({ reference_values: true }, true)).toBe(true);
    expect(showsReferenceLabel({ family: "I", reference_values: false }, true, true)).toBe(false);
  });

  it("keeps an I template id in a link", () => {
    expect(templateIdOf("I1")).toBe("I1");
    expect(templateIdOf("J1")).toBeNull();
  });
});
