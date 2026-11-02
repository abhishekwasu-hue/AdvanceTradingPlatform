import { chartsPath } from "../shell/shellRoutes";

/** NAV-4: the full routes of the option-chain-area panels (a panel's real address and its fallback navigation). */

/** The parameters the Option chain page reads from its address, in the order they are written. */
export const OPTION_CHAIN_KEYS = ["underlying", "expiry", "tf", "basket", "signal", "trade"] as const;

/** `/option-chain?underlying=NIFTY&expiry=...` - the page as its own address reads it (empty values left out). */
export function optionChainPanelPath(p: Readonly<Record<string, string | undefined>>): string {
  const q = new URLSearchParams();
  for (const k of OPTION_CHAIN_KEYS) { const v = p[k]; if (v) q.set(k, v); }
  const query = q.toString();
  return query ? `/option-chain?${query}` : "/option-chain";
}

/** The symbol hub's Futures tab: `/charts/NIFTY%2050?hub=futures&expiry=...` (as fno/contracts.ts adjustHref). */
export function futuresPanelPath(p: Readonly<Record<string, string | undefined>>): string {
  const q = new URLSearchParams({ hub: "futures" });
  if (p.tf) q.set("tf", p.tf);
  if (p.exchange && p.exchange !== "NSE") q.set("exchange", p.exchange);
  if (p.expiry) q.set("expiry", p.expiry);
  return `${chartsPath(p.symbol)}?${q.toString()}`;
}
