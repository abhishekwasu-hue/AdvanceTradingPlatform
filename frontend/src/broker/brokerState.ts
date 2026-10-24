import type { BrokerConnection, BrokerConnectionStatus } from "../types";

/**
 * P2b: what the Broker page, the sidebar dot, the sign-in prompt and the Dashboard's Renew banner read from
 * GET /api/broker/connections. Pure functions, so every state is unit-tested (broker/brokerState.test.ts).
 *
 * `null` connections means "not known" (still loading, or the read failed): then nothing is shown - no dot, no
 * prompt, no banner - rather than a guess.
 */

/** The status a connection is shown with. A data-only connection (an Upstox analytics token) is "connected" unless the
 * server says it is not working: it has nothing to log in to each day, so it never asks for today's login. */
export function shownStatus(c: Pick<BrokerConnection, "status" | "data_only">): BrokerConnectionStatus {
  if (c.data_only && c.status === "needs_login") return "connected";
  return c.status;
}

/** The sidebar dot: green (connected), amber (needs today's login) or red (not working). Several brokers: the one that
 * needs the most attention decides. No broker or not known: no dot. */
export type BrokerDot = "connected" | "needs_login" | "not_working";

export function brokerDot(connections: readonly BrokerConnection[] | null): BrokerDot | null {
  if (!connections || connections.length === 0) return null;
  const shown = connections.map(shownStatus);
  if (shown.includes("not_working")) return "not_working";
  if (shown.includes("needs_login")) return "needs_login";
  return "connected";
}

/** True when the dot asks for attention (amber or red): the phone's More button carries it too. */
export function needsAttention(dot: BrokerDot | null): boolean {
  return dot === "needs_login" || dot === "not_working";
}

/** The dot's colour token and its words for screen readers. Red only for "not working". */
export const DOT_CLASS: Record<BrokerDot, string> = { connected: "bg-up", needs_login: "bg-warn", not_working: "bg-down" };
export const DOT_TEXT: Record<BrokerDot, string> = { connected: "connected", needs_login: "needs login", not_working: "not working" };

/** The one connection that needs today's login and can be renewed here (the first one listed), or null. */
export function renewable(connections: readonly BrokerConnection[] | null): BrokerConnection | null {
  if (!connections) return null;
  return connections.find((c) => c.renew && shownStatus(c) === "needs_login") ?? null;
}

/**
 * The sign-in prompt, shown only while no broker is connected:
 * - "connect": no broker yet (or none working) - "Connect your broker to see live charts and start paper trading";
 * - "renew": a broker that needs today's login - "<Broker> needs today's login";
 * - null: a broker is connected (an analytics-only Upstox counts), or not known yet.
 */
export type BrokerPrompt = { kind: "connect" } | { kind: "renew"; connection: BrokerConnection };

export function promptFor(connections: readonly BrokerConnection[] | null): BrokerPrompt | null {
  if (!connections) return null;
  if (connections.some((c) => shownStatus(c) === "connected")) return null;
  const renew = renewable(connections);
  return renew ? { kind: "renew", connection: renew } : { kind: "connect" };
}

/** The Dashboard banner: one "Renew" for a broker with a daily login that needs today's login, whatever else is
 * connected. Never for a data-only connection. */
export function bannerFor(connections: readonly BrokerConnection[] | null): BrokerConnection | null {
  return renewable(connections);
}

/** The status words on the Broker page's row. */
export function statusText(c: Pick<BrokerConnection, "status" | "data_only">): string {
  switch (shownStatus(c)) {
    case "connected": return "Connected";
    case "needs_login": return "Needs today's login";
    default: return "Not working";
  }
}

/** "Upstox" or "Upstox (second)" - the account label only when there is more than the usual one. */
export function connectionTitle(c: Pick<BrokerConnection, "name" | "account_label">): string {
  const label = (c.account_label ?? "").trim();
  return label && label !== "primary" ? `${c.name} (${label})` : c.name;
}

/** The letters on a broker's tile: "Angel One" -> "AO", "Upstox" -> "U", "Zerodha (Kite)" -> "Z". Plain text, no
 * logo artwork. */
export function monogram(name: string): string {
  // "Zerodha (Kite)" -> "Z": a part in brackets is a product name, not a word of the broker's name.
  const words = name.replace(/\([^)]*\)/g, " ").split(/[^\p{L}\p{N}]+/u).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  return (words[0]?.[0] ?? "?").toUpperCase();
}

/** True when this user may connect, renew or remove brokers (the `brokers:write` scope the server checks). A profile
 * without a scope list (an older server) is left to the server to decide. */
export function canChangeBrokers(user: { scopes?: string[] } | null | undefined): boolean {
  if (!user) return false;
  return !Array.isArray(user.scopes) || user.scopes.includes("brokers:write");
}

/** The one line a member without that permission sees instead of the actions. */
export const ASK_OWNER = "Ask the owner of your organisation to connect a broker.";

/** The broker ids the app knows (the catalog's), for anything read from an address - never echo an unknown one. */
export const KNOWN_BROKERS: Readonly<Record<string, string>> = {
  upstox: "Upstox", zerodha: "Zerodha", fyers: "Fyers", angel_one: "Angel One", shoonya: "Shoonya", dhan: "Dhan", coindcx: "CoinDCX",
  kotak_neo: "Kotak Neo",
};

/** A row that cannot fix itself with Renew: not working, or needs a login it cannot renew - "Connect again" instead. */
export function needsReconnect(c: Pick<BrokerConnection, "status" | "data_only" | "renew">): boolean {
  const s = shownStatus(c);
  return s === "not_working" || (s === "needs_login" && !c.renew);
}
