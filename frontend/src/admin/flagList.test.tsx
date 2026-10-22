import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { FeatureFlags } from "../types";
import FeatureFlagsCard from "./FeatureFlagsCard";
import { blockedBy, groupFlags, statusTone } from "./flagList";

const FLAGS: FeatureFlags = {
  screener_v2: { on: false, tenants: [], description: "Screener v2", plain: "The screener", area: "screens", area_label: "Find setups (screens)",
    turns_on: "Find setups page", depends_on: [], needs: "a broker or stored data", status: "ready", status_label: "Ready for users", default_on: false },
  screener_gallery: { on: false, tenants: [], description: "Gallery", plain: "Ready-made screens to browse", area: "screens", area_label: "Find setups (screens)",
    turns_on: "gallery on Find setups", depends_on: ["screener_v2"], needs: "", status: "ready", status_label: "Ready for users", default_on: false },
  chart_contracts: { on: false, tenants: [], description: "Contracts", plain: "Daily option / future charts with OI", area: "charts", area_label: "Charts and tools",
    turns_on: "contract chart window", depends_on: ["history_backfill"], needs: "the nightly download has run", status: "unfinished", status_label: "Unfinished / needs setup", default_on: false },
  history_backfill: { on: true, tenants: [], description: "Backfill", plain: "Nightly NSE end-of-day download", area: "data", area_label: "Data and alerts",
    turns_on: "nothing visible", depends_on: [], needs: "", status: "behind_the_scenes", status_label: "Ready (behind the scenes)", default_on: false },
  live_trading: { on: false, tenants: [], description: "Live", plain: "New LIVE (real money) bots", area: "trading", area_label: "Trading and risk",
    turns_on: "LIVE mode", depends_on: [], needs: "go-live checklist, two-factor", status: "decision", status_label: "Owner / compliance decision", default_on: true },
};

describe("UX1 admin Feature flags list", () => {
  it("groups by area in a fixed order and sorts rows by their plain name", () => {
    const groups = groupFlags(FLAGS);
    expect(groups.map((g) => g.area)).toEqual(["trading", "charts", "screens", "data"]);
    expect(groups.find((g) => g.area === "screens")!.rows.map((r) => r.name)).toEqual(["screener_gallery", "screener_v2"]);
  });

  it("searches the name, the plain words and the status", () => {
    expect(groupFlags(FLAGS, "gallery").flatMap((g) => g.rows.map((r) => r.name))).toEqual(["screener_gallery"]);
    expect(groupFlags(FLAGS, "unfinished").flatMap((g) => g.rows.map((r) => r.name))).toEqual(["chart_contracts"]);
    expect(groupFlags(FLAGS, "real money").flatMap((g) => g.rows.map((r) => r.name))).toEqual(["live_trading"]);
    expect(groupFlags(FLAGS, "nothing like this")).toEqual([]);
  });

  it("a dependency that is off blocks turning on, by its plain name", () => {
    expect(blockedBy("screener_gallery", FLAGS)).toEqual(["The screener"]);
    expect(blockedBy("chart_contracts", FLAGS)).toEqual([]);                 // history_backfill is on
    expect(blockedBy("screener_v2", FLAGS)).toEqual([]);
    expect(statusTone("ready")).toBe("up");
    expect(statusTone("unfinished")).toBe("warn");
  });

  it("renders status, default, what turns on, needs, and greys the blocked switch", () => {
    const html = renderToStaticMarkup(
      <FeatureFlagsCard flags={FLAGS} tenants={{}} onTenants={() => {}} busy={false} onSet={() => {}} onSaveList={() => {}} />);
    expect(html).toContain('data-flag-status="unfinished"');
    expect(html).toContain("Unfinished / needs setup");
    expect(html).toContain("Default OFF");
    expect(html).toContain("Turns on: gallery on Find setups");
    expect(html).toContain("Needs: The screener");
    expect(html).toContain("Needs The screener first");
    expect(html.match(/data-flag-blocked/g)?.length).toBe(1);
    expect(html).toContain('placeholder="Search flags"');
  });
});
