import { api } from "../api/client";
import type { VerificationSendResult } from "../types";

/**
 * User-friendly pass item 1 (UX audit M13): "Verify your email" is asked for in two places only - one row in the
 * Dashboard's Needs attention list and the Email verification card in Settings › Account - never as a chip on every
 * page. Both send a fresh link the same way and say what happened in the same words.
 */
export function resendText(r: VerificationSendResult): string {
  if (r.already_verified) return "Your email is already verified.";
  return r.sent ? "We sent you a new link. Check your inbox." : "We could not send the email right now. Try again later.";
}

/** Sends a fresh verification link; resolves to the sentence to show (never a server error text). */
export async function resendVerification(): Promise<string> {
  try {
    return resendText(await api.resendVerification());
  } catch {
    return "We could not send the email right now. Try again later.";      // never the server's own text
  }
}
