import { describe, expect, it } from "vitest";
import { STATIC_IP_BROKERS, brokerDisplayName, staticIpBrokerOptions, staticIpRows } from "./staticIps";

describe("staticIpRows", () => {
  it("groups PRIMARY and BACKUP per broker and checks the server IP", () => {
    const rows = staticIpRows({
      server_egress_ip: "203.0.113.10", required_for_live: false, max_changes_per_week: 1, warnings: [],
      ips: [
        { broker_name: "zerodha", role: "PRIMARY", ip: "198.51.100.1", registered_at: null, updated_at: null },
        { broker_name: "upstox", role: "BACKUP", ip: "203.0.113.10", registered_at: null, updated_at: null },
        { broker_name: "upstox", role: "PRIMARY", ip: "198.51.100.2", registered_at: null, updated_at: null },
      ],
    });
    expect(rows).toEqual([
      { broker: "upstox", primary: "198.51.100.2", backup: "203.0.113.10", serverIpRegistered: true },
      { broker: "zerodha", primary: "198.51.100.1", backup: null, serverIpRegistered: false },
    ]);
  });

  it("does not judge when the server IP is not configured", () => {
    const rows = staticIpRows({ server_egress_ip: null, required_for_live: false, max_changes_per_week: 1, warnings: [],
      ips: [{ broker_name: "upstox", role: "PRIMARY", ip: "198.51.100.2", registered_at: null, updated_at: null }] });
    expect(rows[0].serverIpRegistered).toBeNull();
  });

  it("offers brokers by their registry names (angel_one, not angelone)", () => {
    expect(STATIC_IP_BROKERS).toContain("angel_one");
    expect(STATIC_IP_BROKERS).not.toContain("angelone");
    for (const b of STATIC_IP_BROKERS) expect(b).toMatch(/^[a-z0-9_]+$/);
  });

  it("builds the Broker options from the registry list: registry ids as values, display names as labels", () => {
    // What GET /api/broker/available returns (backend/app/brokers/registry.py, BROKER_ADAPTERS order).
    const registry = ["zerodha", "upstox", "shoonya", "angel_one", "fyers", "dhan", "coindcx"];
    const options = staticIpBrokerOptions(registry);
    expect(options.map((o) => o.value)).toEqual(registry);
    expect(options).toContainEqual({ value: "angel_one", label: "Angel One" });
    expect(options.map((o) => o.value)).not.toContain("angelone");
    expect(options.find((o) => o.value === "shoonya")?.label).toBe("Shoonya / Finvasia");
  });

  it("falls back to the built-in registry names before the list loads, never a legacy spelling", () => {
    for (const before of [null, undefined, []]) {
      const values = staticIpBrokerOptions(before).map((o) => o.value);
      expect(values).toEqual(STATIC_IP_BROKERS);
      expect(values).toContain("angel_one");
      expect(values).not.toContain("angelone");
    }
  });

  it("shows an unknown id readably and drops malformed ones", () => {
    expect(brokerDisplayName("kotak_neo")).toBe("Kotak Neo");
    expect(staticIpBrokerOptions(["Upstox ", "upstox", "bad name!"]).map((o) => o.value)).toEqual(["upstox"]);
  });
});
