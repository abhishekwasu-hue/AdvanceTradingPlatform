import { describe, expect, it } from "vitest";
import { staticIpRows } from "./staticIps";

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
});
