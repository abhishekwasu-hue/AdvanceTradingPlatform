/**
 * UX2 fix 5 ("Telegram in one tap", docs/design/UX_AUDIT_FULL.md problem 7): what the Connect Telegram row shows. The
 * server (`GET /api/alerts/telegram/connect`, backend app/alerts/platform_bot_routes.py) says whether this server has
 * the platform's bot, and whether the organisation is connected through it or through its own bot. Pure functions,
 * unit-tested; the row itself is `TelegramConnectV2.tsx`.
 */

export interface TelegramConnectState {
  /** This server has the platform's Telegram bot. */
  available: boolean;
  /** The organisation's Telegram alerts go through the platform's bot to a chat that pressed Start. */
  connected: boolean;
  /** The organisation sends Telegram alerts with its own bot (set up under Settings → Advanced). */
  own_bot: boolean;
  /** The last message (the welcome or a test) went through. */
  working: boolean;
}

export type TelegramView = "unavailable" | "own_bot" | "connected" | "waiting" | "connect";

/** How often the row asks the server while the trader is in Telegram, and for how long at most. */
export const POLL_MS = 3000;
export const WAIT_MAX_MS = 15 * 60 * 1000;

export function telegramView(state: TelegramConnectState, waiting: boolean): TelegramView {
  if (state.own_bot) return "own_bot";
  if (state.connected) return "connected";
  if (!state.available) return "unavailable";
  return waiting ? "waiting" : "connect";
}

export const TELEGRAM_TEXT: Record<TelegramView, string> = {
  unavailable: "Telegram alerts are not available yet. Use app notifications or email for now.",
  own_bot: "Your organisation's own Telegram bot sends your alerts.",
  connected: "Alerts come to your Telegram chat.",
  waiting: "Telegram is open. Press Start there, then come back. This page updates by itself.",
  connect: "Get alerts on your phone in Telegram. One tap, then press Start.",
};

/** The Get started step and the Setup wizard count Telegram as done once a message went through. */
export function telegramDone(state: TelegramConnectState | null): boolean {
  return !!state && (state.connected || state.own_bot) && state.working;
}
