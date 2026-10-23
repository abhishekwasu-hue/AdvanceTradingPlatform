/**
 * Readiness C13 (flags `ui_v2` + `referrals`): the invite link's code, kept from the moment a visitor arrives with
 * `?ref=CODE` until his new account has named it once. Only in this browser (localStorage), wrapped in try / catch: a
 * private window or blocked storage simply loses the code, never the page.
 */
import { request } from "../api/client";

export const REF_PARAM = "ref";
export const REF_STORE = "atp.referral.code";
const CODE = /^[A-HJ-NP-Z2-9]{8}$/;

export interface ReferralCard {
  /** null until he asks for his invite link. */
  code: string | null;
  link: string | null;
  /** The operator's reward wording, or null. */
  reward_text: string | null;
  joined: number;
}

export const referralApi = {
  card: () => request<ReferralCard>("/growth/referral"),
  createCode: () => request<ReferralCard>("/growth/referral/code", { method: "POST" }),
  claim: (code: string) => request<{ claimed: boolean }>("/growth/referral/claim", { method: "POST", body: JSON.stringify({ code }) }),
};

/** The code in an address's query (`?ref=abcd-2345` -> `ABCD2345`), or null when it is not a well-formed code. */
export function codeFromSearch(search: string): string | null {
  const raw = new URLSearchParams(search).get(REF_PARAM) ?? "";
  const code = raw.toUpperCase().replace(/[^A-Z0-9]/g, "");
  return CODE.test(code) ? code : null;
}

export function rememberCode(code: string): void {
  try { window.localStorage.setItem(REF_STORE, code); } catch { /* storage blocked: the code is simply not kept */ }
}

export function rememberedCode(): string | null {
  try {
    const code = window.localStorage.getItem(REF_STORE);
    return code && CODE.test(code) ? code : null;
  } catch {
    return null;
  }
}

export function forgetCode(): void {
  try { window.localStorage.removeItem(REF_STORE); } catch { /* nothing to forget */ }
}

/** "3 people joined" in plain words. */
export function joinedLine(n: number): string {
  if (n <= 0) return "Nobody has joined through your link yet.";
  return n === 1 ? "1 person joined through your link." : `${n} people joined through your link.`;
}
