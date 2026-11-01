/**
 * REV-1 M2 (Notifications): an event kind in plain words, never the server's code ("DAILY_LOSS_LIMIT").
 * The codes are backend/app/core/enums.py NotificationType.
 */
const EVENT_WORDS: Readonly<Record<string, string>> = {
  ENTRY: "Trade opened",
  EXIT: "Trade closed",
  REJECTION: "Order not placed",
  BROKER_DISCONNECT: "Broker disconnected",
  TOKEN_EXPIRED: "Broker login ran out",
  RISK_REJECTION: "Held back by your risk limits",
  DAILY_LOSS_LIMIT: "Daily loss limit reached",
  EMERGENCY_EXIT: "Emergency exit",
  SYSTEM_FAILURE: "Something went wrong",
  SECURITY: "Account security",
  AI_PROPOSAL: "Suggestion to review",
  MARKETPLACE: "Marketplace",
  EOD_SUMMARY: "Day summary",
  SCREEN_ALERT: "Your alert",
  NEWS_ALERT: "News",
  THESIS_REPORT: "Market view report",
  OI_BANNER: "Open interest update",
  OI_COLLECTOR: "Open interest data",
  SETTLEMENT_WARNING: "Settlement reminder",
  STRUCTURE_INCOMPLETE: "Order only partly placed",
  COPILOT_WATCH: "Copilot watch",
};

/** "Daily loss limit reached"; an unknown code becomes words ("NEW_KIND" -> "New kind"), never the code itself. */
export function notificationEventWords(code: string | null | undefined): string {
  const key = (code ?? "").trim().toUpperCase();
  if (!key) return "";
  if (EVENT_WORDS[key]) return EVENT_WORDS[key];
  const words = key.replace(/[_.]+/g, " ").trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}
