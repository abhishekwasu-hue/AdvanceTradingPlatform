import { request } from "../api/client";
import { feedbackMessage, feedbackScreen, feedbackVersion } from "../feedback/feedback";
import type { ServerHelpArticle } from "./helpCentre";
import type { LastServerError } from "./lastError";

/**
 * C12: the support desk reads and "Report a problem" (`/api/support/*`; 404 while `ui_v2` or `help_centre` is off).
 * The report sends a closed list of fields - the message the user typed, the screen path, the app version, the time
 * and, when the user leaves it ticked, the last server error's reference with its screen and time. Never a form's
 * contents, a header, a token or an address query (the server refuses any other field and scrubs the message).
 */

export const HELP_CENTRE_FLAG = "help_centre";

export interface SupportContact {
  configured: boolean;
  market_open: boolean;
  next_open: string | null;
  whatsapp_url: string | null;
  email: string | null;
}

export interface ReportBody {
  message: string;
  app_version: string;
  screen: string;
  client_time: string;
  error_id?: string;
  error_screen?: string;
  error_at?: string;
}

export interface ReportResult { id: number; created_at: string | null; status: string; attached: Record<string, string> }

/** The body "Report a problem" sends, or null when the message cannot be sent (empty or too long). */
export function reportBody(text: string, pathname: string, version: string, lastError: LastServerError | null, now: Date = new Date()): ReportBody | null {
  const message = feedbackMessage(text);
  if (!message) return null;
  const body: ReportBody = { message, app_version: feedbackVersion(version), screen: feedbackScreen(pathname), client_time: now.toISOString() };
  if (lastError) {
    body.error_id = lastError.id;
    body.error_screen = feedbackScreen(lastError.page);
    body.error_at = lastError.at;
  }
  return body;
}

/** A wa.me link or a mailto: address only - anything else from the server is dropped. */
export function safeWhatsapp(url: string | null): string | null {
  return url && /^https:\/\/wa\.me\/\d{8,15}$/.test(url) ? url : null;
}
export function safeEmail(email: string | null): string | null {
  return email && /^[^\s@<>"]+@[^\s@<>"]+\.[A-Za-z]{2,}$/.test(email) ? email : null;
}

export const deskApi = {
  help: () => request<{ articles: ServerHelpArticle[] }>("/support/help"),
  contact: () => request<SupportContact>("/support/contact"),
  report: (body: ReportBody) => request<ReportResult>("/support/report", { method: "POST", body: JSON.stringify(body) }),
};
