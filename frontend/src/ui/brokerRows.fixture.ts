import type { BrokerTokenInfo } from "../types";

/** UI2-0 tests and stories: a token-status row as GET /api/broker/token-status returns it (backend/app/brokers/routes.py).
 * Shape only - never shown to a trader. */
export function tokenRow(broker: string, over: Partial<BrokerTokenInfo> = {}): BrokerTokenInfo {
  return {
    broker_name: broker, account_label: "primary", token_status: "EXPIRED", token_expires_at: null, last_verified_at: null,
    needs_login: true, oauth_supported: broker === "upstox", oauth_callback_url: null,
    login_url_supported: broker === "zerodha" || broker === "fyers", code_param: null, ...over,
  };
}
