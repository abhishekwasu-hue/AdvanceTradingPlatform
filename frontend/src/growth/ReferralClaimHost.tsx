import { useEffect } from "react";
import { forgetCode, referralApi, rememberedCode } from "./referral";

/**
 * Readiness C13: mounted once by the v2 shell while `referrals` is on. A new account that arrived through an invite link
 * (`?ref=CODE`, kept by App.tsx) names it once. Whatever the server answers (claimed, already used, too old), the kept
 * code is forgotten, so it is never sent twice.
 */
export default function ReferralClaimHost() {
  useEffect(() => {
    const code = rememberedCode();
    if (!code) return;
    referralApi.claim(code).catch(() => undefined).finally(forgetCode);
  }, []);
  return null;
}
