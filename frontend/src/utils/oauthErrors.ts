/** The Upstox OAuth callback returns to the app with `?broker=upstox&error=<code>`; these are the words shown for each code.
 * Unknown codes are shown as they are. */
const MESSAGES: Record<string, string> = {
  not_holder:
    "this broker connection was linked by another member of your organisation. With the bring-your-own-broker data tier on, only they can log in to it - ask them to log in, or ask an owner to remove it before you link your own account.",
  changed_during_login: "the broker connection was changed while you were logging in, so nothing was stored. Log in again.",
  exchange_failed: "the broker did not accept the login code. Log in again.",
  credentials_missing: "store the API key and secret under Settings > Brokers first.",
  invalid_state: "the login link expired or was not started here. Start the login again from Settings.",
  missing_code_or_state: "the broker returned without a login code. Start the login again from Settings.",
  no_token_returned: "the broker returned no session token. Log in again.",
};

export function oauthErrorText(code: string): string {
  return MESSAGES[code] ?? code;
}
