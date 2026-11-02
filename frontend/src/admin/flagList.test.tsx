import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { FeatureFlags } from "../types";
import FeatureFlagsCard from "./FeatureFlagsCard";
import { blockedBy, flagState, groupFlags, stateLine, stateTone, statusTone } from "./flagList";

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
    expect(groups.map((g) => g.area)).toEqual(["charts", "screens", "trading", "data"]);   // ADM-1: finer groups first
    expect(groups.find((g) => g.area === "screens")!.rows.map((r) => r.name)).toEqual(["screener_gallery", "screener_v2"]);
  });

  it("ADM-1: groups by the finer category when the server sends one, and filters by state and status", () => {
    const flags: FeatureFlags = {
      ...FLAGS,
      live_trading: { ...FLAGS.live_trading, group: "safety", group_label: "Safety and LIVE" },
      screener_gallery: { ...FLAGS.screener_gallery, group: "screen_library", group_label: "Screens: ready-made screens and ideas" },
    };
    const groups = groupFlags(flags);
    expect(groups.map((g) => g.area)).toEqual(["safety", "charts", "screens", "screen_library", "data"]);
    expect(groups[0].label).toBe("Safety and LIVE");
    const names = (filter: Parameters<typeof groupFlags>[2]) => groupFlags(flags, "", filter).flatMap((g) => g.rows.map((r) => r.name));
    expect(names("on")).toEqual(["history_backfill"]);
    expect(names("setup")).toEqual(["chart_contracts"]);
    expect(names("decision")).toEqual(["live_trading"]);
    expect(names("off")).not.toContain("history_backfill");
  });

  it("ADM-1: each category is folded with its count; the flag name and the allow-list sit in Details", () => {
    const html = renderToStaticMarkup(
      <FeatureFlagsCard flags={FLAGS} tenants={{}} onTenants={() => {}} busy={false} onSet={() => {}} onSaveList={() => {}} />);
    expect(html).toContain("1 of 5 switches on, in 4 categories");
    expect(html).toMatch(/<details aria-label="Data and alerts" data-flag-area="data"[^>]*>/);
    expect(html).not.toMatch(/<details aria-label="Data and alerts"[^>]*open/);
    expect(html).toContain("1 of 1 on");
    expect(html.match(/data-flag-details/g)?.length).toBe(5);
    expect(html).toContain("Keep it on for these organisations only");
    expect(html.match(/data-flag-filter=/g)?.length).toBe(6);                   // M80: + "Not built yet"
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

  it("item 7: states why users do not see a feature on this server (admin only)", () => {
    const flags: FeatureFlags = { ...FLAGS, chart_contracts: { ...FLAGS.chart_contracts, unavailable_reason: "The chart analysis engine (private package) is not installed on this server, so users do not see this feature." } };
    const html = renderToStaticMarkup(
      <FeatureFlagsCard flags={flags} tenants={{}} onTenants={() => {}} busy={false} onSet={() => {}} onSaveList={() => {}} />);
    expect(html.match(/data-flag-unavailable/g)?.length).toBe(1);
    expect(html).toContain("is not installed on this server");
  });
  it("P2-1: shows each flag's fate and has no Turn on for a flag removed from view", () => {
    const flags: FeatureFlags = {
      ...FLAGS,
      chart_contracts: { ...FLAGS.chart_contracts, fate: "removed_from_view", fate_note: "Removed from view: superseded" },
      screener_gallery: { ...FLAGS.screener_gallery, fate: "finish", fate_note: "In the trader pack; finishing in P2-3" },
    };
    const html = renderToStaticMarkup(
      <FeatureFlagsCard flags={flags} tenants={{}} onTenants={() => {}} busy={false} onSet={() => {}} onSaveList={() => {}} />);
    expect(html).toContain("In the trader pack; finishing in P2-3");
    expect(html).toContain("Removed from view: superseded");
    const before = renderToStaticMarkup(
      <FeatureFlagsCard flags={FLAGS} tenants={{}} onTenants={() => {}} busy={false} onSet={() => {}} onSaveList={() => {}} />);
    expect(html.match(/>Turn on</g)?.length).toBe((before.match(/>Turn on</g)?.length ?? 0) - 1);
  });

  it("M55 S1: the LIVE switches are read-only for anyone but the owner; the owner gets a typed confirmation", () => {
    const live = (canSwitch: boolean): FeatureFlags => ({
      live_trading: { ...FLAGS.live_trading, owner_only: true, can_switch_live: canSwitch, confirm_text: "ENABLE LIVE", opens_live_when_on: true },
      phase0_paper_only: { ...FLAGS.live_trading, on: true, plain: "PAPER-only lock", owner_only: true, can_switch_live: canSwitch,
        confirm_text: "ENABLE LIVE", opens_live_when_on: false },
    });
    const render = (flags: FeatureFlags) => renderToStaticMarkup(
      <FeatureFlagsCard flags={flags} tenants={{}} onTenants={() => {}} busy={false} onSet={() => {}} onSaveList={() => {}} />);
    const other = render(live(false));
    expect(other.match(/Only the owner can switch LIVE on/g)?.length).toBe(2);
    expect(other).not.toMatch(/>Turn on<|>Turn off<|data-flag-confirm/);
    expect(other).not.toContain("Keep it on for these organisations only");
    const owner = render(live(true));
    expect(owner).not.toContain("Only the owner can switch LIVE on");
    expect(owner.match(/data-flag-confirm/g)?.length).toBe(2);
    expect(owner).toContain('placeholder="Type ENABLE LIVE"');
    expect(owner.match(/<button[^>]*disabled=""[^>]*>Turn (on|off)<\/button>/g)?.length).toBe(2);   // until typed
  });

  it("M55 S1b: LIVE for organisations - read-only for anyone but the owner; Grant / Revoke wait for the confirmation", () => {
    const live = (canSwitch: boolean, orgs: { id: number; name: string }[]): FeatureFlags => ({
      live_trading: { ...FLAGS.live_trading, tenants: orgs.map((o) => o.id), owner_only: true, can_switch_live: canSwitch,
        confirm_text: "ENABLE LIVE", opens_live_when_on: true, live_orgs: orgs },
    });
    const render = (flags: FeatureFlags) => renderToStaticMarkup(
      <FeatureFlagsCard flags={flags} tenants={{}} onTenants={() => {}} busy={false} onSet={() => {}} onSaveList={() => {}}
        onGrantLive={() => {}} />);
    const empty = render(live(true, []));
    expect(empty).toContain("LIVE for organisations");
    expect(empty).toContain("No organisation has LIVE.");
    expect(empty).toMatch(/<button[^>]*disabled=""[^>]*>Grant<\/button>/);
    const other = render(live(false, [{ id: 7, name: "Desk Seven" }]));
    expect(other).toContain("Desk Seven");
    expect(other).toContain("Only the owner can grant or revoke LIVE.");
    expect(other).not.toMatch(/>Grant<|>Revoke<|data-live-org-confirm/);
    expect(other).not.toContain("(1 allowed)");
    const owner = render(live(true, [{ id: 7, name: "Desk Seven" }]));
    expect(owner).toMatch(/<button[^>]*disabled=""[^>]*>Revoke<\/button>/);
    expect(owner).toContain("data-live-org-confirm");
  });
});

describe("M80 truthful flag status", () => {
  const M80: FeatureFlags = {
    ...FLAGS,
    screener_v2: { ...FLAGS.screener_v2, state: "ready", state_label: "Ready", state_reason: "", state_where: "" },
    chart_contracts: { ...FLAGS.chart_contracts, state: "needs_setup", state_label: "Needs your setup", state_reason: "Telegram bot",
      state_where: "Settings › Alerts", checks: [{ id: "telegram", label: "Telegram set up", ok: false }] },
    screener_gallery: { ...FLAGS.screener_gallery, depends_on: [], state: "not_built", state_label: "Not built yet",
      state_reason: "No screen to start a study", state_order: "FN5" },
    history_backfill: { ...FLAGS.history_backfill, state: "behind_the_scenes", state_label: "Ready (behind the scenes)",
      checks: [{ id: "broker", label: "Broker connected", ok: true }] },
    live_trading: { ...FLAGS.live_trading, state: "decision", state_label: "Owner decision", state_reason: "LIVE only on your words" },
  };
  const render = (flags: FeatureFlags) => renderToStaticMarkup(
    <FeatureFlagsCard flags={flags} tenants={{}} onTenants={() => {}} busy={false} onSet={() => {}} onSaveList={() => {}} />);

  it("reads the state, falls back to the old label, and filters Needs setup / Not built yet / Owner decision by it", () => {
    expect(flagState(M80.chart_contracts)).toBe("needs_setup");
    expect(flagState(FLAGS.chart_contracts)).toBe("needs_setup");          // an old server: "unfinished" reads as setup
    expect(flagState(FLAGS.live_trading)).toBe("decision");
    const names = (filter: Parameters<typeof groupFlags>[2]) => groupFlags(M80, "", filter).flatMap((g) => g.rows.map((r) => r.name));
    expect(names("setup")).toEqual(["chart_contracts"]);
    expect(names("not_built")).toEqual(["screener_gallery"]);
    expect(names("decision")).toEqual(["live_trading"]);
    expect(groupFlags(M80, "settings › alerts").flatMap((g) => g.rows.map((r) => r.name))).toEqual(["chart_contracts"]);
    expect(stateTone("not_built")).toBe("down");
    expect(stateTone("needs_setup")).toBe("warn");
    expect(stateLine(M80.screener_gallery)).toEqual({ reason: "No screen to start a study (FN5)", where: "" });
  });

  it("shows the chip, the item and where, and the live checks", () => {
    const html = render(M80);
    expect(html).toContain('data-flag-state="needs_setup"');
    expect(html).toContain("Needs your setup");
    expect(html).toContain("Telegram bot");
    expect(html).toContain("› Settings › Alerts");
    expect(html).toMatch(/data-flag-check="telegram" data-ok="false"[^>]*>.*✗.*Telegram set up/);
    expect(html).toMatch(/data-flag-check="broker" data-ok="true"[^>]*>.*✓.*Broker connected/);
    expect(html).not.toContain("Unfinished / needs setup");
  });

  it("disables Turn on for a flag that is not built yet, with the reason; Turn off stays", () => {
    const html = render(M80);
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*title="Not built yet: No screen to start a study \(FN5\)"[^>]*>Turn on<\/button>/);
    const on = render({ ...M80, screener_gallery: { ...M80.screener_gallery, on: true } });
    expect(on).toMatch(/data-flag="screener_gallery"[\s\S]*?<button type="button" class="[^"]*">Turn off<\/button>/);
  });
});
