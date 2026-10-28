import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { RELEASES, latestRelease } from "./whatsNew";
import { CARD_LINE_WORDS } from "../ui/textBudget";

describe("AUD-V7 What's new", () => {
  it("has one short line per release, newest first, each with its CHANGELOG section", () => {
    const changelog = readFileSync(resolve(__dirname, "../../../CHANGELOG.md"), "utf8");
    expect(latestRelease()?.version).toBe(RELEASES[0].version);
    for (const r of RELEASES) {
      expect(r.version).toMatch(/^\d+\.\d+\.\d+$/);
      expect(changelog).toContain(`## v${r.version} - ${r.date}`);
      expect(r.line.split(/\s+/).filter((w) => /\p{L}/u.test(w)).length).toBeLessThanOrEqual(CARD_LINE_WORDS);
    }
  });
});
