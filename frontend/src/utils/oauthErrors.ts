/** The Upstox OAuth callback returns to the app with `?broker=upstox&error=<code>`; these are the words shown for each code.
 * An unknown code gets one generic sentence: text from the address is never shown (P2b review). */
const MESSAGES: Record<string, string> = {
  not_holder:
    "this broker connection was linked by another member of your organisation. With the bring-your-own-broker data tier on, only they can log in to it - ask them to log in, or ask an owner to remove it before you link your own account.",
  changed_during_login: "the broker connection was changed while you were logging in, so nothing was stored. Log in again.",
  exchange_failed: "the broker did not accept the login code. Log in again.",
  credentials_missing: "connect Upstox on the Broker page first.",
  invalid_state: "the login link expired or was not started here. Start the login again from the Broker page.",
  missing_code_or_state: "the broker returned without a login code. Start the login again from the Broker page.",
  no_token_returned: "the broker returned no session token. Log in again.",
};

export const UNKNOWN_ERROR = "the login did not finish. Start it again from the Broker page.";

export function oauthErrorText(code: string): string {
  return Object.prototype.hasOwnProperty.call(MESSAGES, code) ? MESSAGES[code] : UNKNOWN_ERROR;
}
