import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { PAGES, pathFor } from "./routes";
import {
  LEARN_V2_ONLY, MARKETS_ADDRESS, SETUP_ADDRESS, TERMINAL_ADDRESS, TRADING_DEFAULTS_ADDRESS, V2_ALIASES, V2_MOVED, V2_RETIRED_ALIASES,
  WATCHLIST_ADDRESS,
} from "./shell/aliases";
import { SETTINGS_SECTIONS } from "./settings/paths";
import { IDEAS_ADDRESS } from "./ideas/model";
import { LEARN_ADDRESS } from "./learn/model";
import { COPILOT_TABS, FOLDED_TABS } from "./copilot/tabs";

/**
 * M63 I-2: docs/FEATURE_INVENTORY.md lists every page address - each page of routes.ts at its `/<id>` address and every
 * v2 shell address (merged pages, retired aliases, v2-only pages, Settings sections, Copilot tabs, Charts, Status) - in
 * backticks. A PR that adds a page or an address adds its row to the inventory.
 */
const inventory = readFileSync("../docs/FEATURE_INVENTORY.md", "utf8");
const listed = new Set([...inventory.matchAll(/`(\/[^`\s]*)`/g)].map((m) => m[1]));

const v2Addresses = [
  ...V2_ALIASES.flatMap((a) => [a.path, a.old]),
  ...V2_RETIRED_ALIASES.map((a) => a.path),
  // M60: the moved addresses and their new homes, and Learn's v2-only tabs.
  ...V2_MOVED.flatMap((m) => [m.from, m.to]), ...LEARN_V2_ONLY, "/copilot/analyze",
  SETUP_ADDRESS, TRADING_DEFAULTS_ADDRESS, MARKETS_ADDRESS, TERMINAL_ADDRESS, WATCHLIST_ADDRESS, IDEAS_ADDRESS, LEARN_ADDRESS,
  ...SETTINGS_SECTIONS.map((s) => s.path),
  ...[...COPILOT_TABS, ...FOLDED_TABS].map((t) => `/copilot/${t}`),
  "/charts", "/charts/:symbol", "/status",
];

describe("feature inventory coverage", () => {
  it("finds the pages (a broken import must not pass vacuously)", () => {
    expect(PAGES.length).toBeGreaterThan(20);
    expect(listed.size).toBeGreaterThan(50);
  });

  it("lists every page address of routes.ts", () => {
    expect(PAGES.map(pathFor).filter((p) => !listed.has(p))).toEqual([]);
  });

  it("lists every v2 shell address", () => {
    expect(v2Addresses.filter((p) => !listed.has(p))).toEqual([]);
  });
});
