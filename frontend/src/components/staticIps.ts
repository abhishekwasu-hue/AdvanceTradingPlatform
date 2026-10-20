import type { StaticIpOverview } from "../types";

/** Brokers offered in the Static IP card until the registry's own list (GET /api/broker/available) has loaded, spelled
 * exactly as the backend registry names them (`app/brokers/registry.py`): the LIVE gate compares the registered row's
 * broker_name with the deployment's, so `angelone` would never match `angel_one`. */
export const STATIC_IP_BROKERS = ["upstox", "zerodha", "fyers", "dhan", "angel_one", "shoonya"];

/** How a registry broker id is shown to a person (the same names as `backend/app/market_data/broker_data_terms.json`). */
export const BROKER_DISPLAY_NAMES: Record<string, string> = {
  upstox: "Upstox", zerodha: "Zerodha", fyers: "Fyers", dhan: "Dhan", angel_one: "Angel One", shoonya: "Shoonya / Finvasia",
  coindcx: "CoinDCX",
};

/** A broker's display name; an id with no entry is shown with underscores as spaces and each word capitalised. */
export function brokerDisplayName(id: string): string {
  return BROKER_DISPLAY_NAMES[id] ?? id.split("_").filter(Boolean).map((w) => w[0].toUpperCase() + w.slice(1)).join(" ");
}

export type BrokerOption = { value: string; label: string };

/** The Broker select's options: the registry's broker ids as values (never a legacy spelling), with display names as
 * labels. `registry` is the list GET /api/broker/available returned; empty or not loaded yet, the built-in list is used. */
export function staticIpBrokerOptions(registry: readonly string[] | null | undefined): BrokerOption[] {
  const ids = registry && registry.length ? registry : STATIC_IP_BROKERS;
  return Array.from(new Set(ids.map((b) => b.trim().toLowerCase()).filter((b) => /^[a-z0-9_]+$/.test(b))))
    .map((value) => ({ value, label: brokerDisplayName(value) }));
}

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
