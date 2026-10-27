/** B8: what the "Notifications on this phone" card says, decided before Android is asked anything. */
export type PhoneState =
  | "server-off"   // the server has no FCM key yet
  | "needs-update" // this app build has no notification plug-in yet
  | "denied"       // the user blocked notifications for the app in Android
  | "ready";       // can turn on (or already on)

export function phoneState(input: { configured: boolean; available: boolean; permission: string }): PhoneState {
  if (!input.configured) return "server-off";
  if (!input.available || input.permission === "unavailable") return "needs-update";
  if (input.permission === "denied") return "denied";
  return "ready";
}

export const PHONE_STATE_TEXT: Record<Exclude<PhoneState, "ready">, string> = {
  "server-off": "Phone notifications are not set up on this server yet. Your alerts still reach you on Telegram.",
  "needs-update": "Notifications on this phone come with the next app update. Until then your alerts reach you on Telegram.",
  denied: "Notifications are blocked for AiAlgoDesk on this phone. Open Android Settings, then Apps, AiAlgoDesk, Notifications, and allow them.",
};

/** The four kinds in the order the card lists them (backend app/alerts/push_kinds.py PHONE_KINDS). */
export const PHONE_KINDS: { id: string; label: string }[] = [
  { id: "signals", label: "Signals from your own strategies" },
  { id: "approvals", label: "Decisions waiting for you" },
  { id: "exits", label: "Positions closed" },
  { id: "renew", label: "Broker login reminders" },
];

/** Ticking or unticking one kind; the result is what the server stores for that phone. */
export function toggleKind(current: readonly string[], id: string): string[] {
  const next = new Set(current);
  if (next.has(id)) next.delete(id); else next.add(id);
  return PHONE_KINDS.map((k) => k.id).filter((k) => next.has(k));
}
