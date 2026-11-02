/** NAV-2: the full routes of the Copilot-area panels (a panel's real address and its fallback navigation). */

export function copilotToolPath(tab?: string, symbol?: string, action?: string): string {
  const base = tab ? `/copilot/${tab}` : "/copilot";
  const q = [symbol && `symbol=${encodeURIComponent(symbol)}`, action && `action=${encodeURIComponent(action)}`].filter(Boolean);   // NAV-5a: + a Watchtower proposal
  return q.length ? `${base}?${q.join("&")}` : base;
}

export function signalsPanelPath(proposal?: string): string {
  return proposal ? `/signals?proposal=${encodeURIComponent(proposal)}` : "/signals";
}
