/**
 * REV-1 M2 (Team): a member permission in plain words. The permission codes ("trading:write") are for platform
 * administrators only and never shown to an organisation owner. Codes: backend/app/auth/scopes.py SCOPES.
 */
export interface PermissionWords { label: string; line: string; live?: true }

const WORDS: Readonly<Record<string, PermissionWords>> = {
  "trading:write": { label: "Run trading", line: "Start, change and stop trading bots, decide on signals and use the emergency stop." },
  "trading:live": { label: "Real-money trading", line: "Start or resume trading bots that trade with real money.", live: true },
  "strategies:write": { label: "Build strategies", line: "Create and edit the organisation's own strategies." },
  "brokers:write": { label: "Broker accounts", line: "Connect, refresh or remove broker accounts." },
  "alerts:write": { label: "Alerts", line: "Choose how alerts reach the team." },
  "risk:write": { label: "Risk limits", line: "Change the risk limits." },
  "billing:manage": { label: "Billing", line: "Change the plan, pay invoices and see billing history." },
  "team:manage": { label: "Team", line: "Invite and remove members and change their roles." },
  "api_keys:manage": { label: "Keys for other programs", line: "Create and remove keys that let other programs connect." },
  "marketplace:publish": { label: "Marketplace", line: "List strategies on the marketplace." },
  "ai:manage": { label: "AI", line: "Set up the AI and approve its drafts and suggestions." },
  "analytics:read": { label: "See results", line: "See positions, trades, performance and reports." },
};

/** Plain words for a permission; an unknown one gets the server's description, never its code. */
export function permissionWords(scope: string, serverDescription: string): PermissionWords {
  return WORDS[scope] ?? { label: serverDescription || "Other permission", line: "" };
}

/** The permissions an owner may see: everything but platform administration, and nothing about LIVE in Phase 0. */
export function shownPermission(scope: string, liveHidden: boolean): boolean {
  if (scope === "admin:platform") return false;
  return !(liveHidden && WORDS[scope]?.live);
}

/** A routing policy's help without the word LIVE while Phase 0 hides it. */
export function routingHelp(help: string | undefined, liveHidden: boolean): string {
  return liveHidden ? (help ?? "").replace(/\s*\bLIVE\b\s*/g, " ").replace(/\s+/g, " ").trim() : help ?? "";
}
