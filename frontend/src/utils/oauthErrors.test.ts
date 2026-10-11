import { describe, expect, it } from "vitest";
import { oauthErrorText } from "./oauthErrors";

describe("OAuth error words (BT1b)", () => {
  it("explains the holder rule and a login that raced a change", () => {
    expect(oauthErrorText("not_holder")).toContain("linked by another member");
    expect(oauthErrorText("not_holder")).not.toContain("account label");
    expect(oauthErrorText("changed_during_login")).toContain("nothing was stored");
  });
  it("shows unknown codes as they are", () => {
    expect(oauthErrorText("something_new")).toBe("something_new");
  });
});
