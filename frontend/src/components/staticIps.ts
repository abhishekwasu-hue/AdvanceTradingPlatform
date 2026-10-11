import type { StaticIpOverview } from "../types";

export type StaticIpRow = { broker: string; primary: string | null; backup: string | null; serverIpRegistered: boolean | null };

/** Part D4: one row per broker (PRIMARY / BACKUP) and whether this server's egress IP is among them. */
export function staticIpRows(data: StaticIpOverview): StaticIpRow[] {
  const brokers = Array.from(new Set(data.ips.map((r) => r.broker_name))).sort();
  return brokers.map((broker) => {
    const mine = data.ips.filter((r) => r.broker_name === broker);
    const primary = mine.find((r) => r.role === "PRIMARY")?.ip ?? null;
    const backup = mine.find((r) => r.role === "BACKUP")?.ip ?? null;
    const server = data.server_egress_ip;
    return { broker, primary, backup, serverIpRegistered: server ? [primary, backup].includes(server) : null };
  });
}
