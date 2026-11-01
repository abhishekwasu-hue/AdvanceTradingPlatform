/** NAV-2: the full routes of the Copilot-area panels (a panel's real address and its fallback navigation). */

export function copilotToolPath(tab?: string, symbol?: string): string {
  const base = tab ? `/copilot/${tab}` : "/copilot";
  return symbol ? `${base}?symbol=${encodeURIComponent(symbol)}` : base;
}

export function signalsPanelPath(proposal?: string): string {
  return proposal ? `/signals?proposal=${encodeURIComponent(proposal)}` : "/signals";
}
