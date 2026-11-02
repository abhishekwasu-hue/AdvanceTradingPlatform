import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { BrokerSignIn, SignInOptionsList, brokerOptions } from "./SocialButtons";
import { brokerInitials, isBrokerProvider, providerLabel, providersFrom, type SignInProvider } from "./social";

const UPSTOX: SignInProvider = { id: "broker:upstox", label: "Upstox", kind: "broker", start: "/auth/broker/upstox/start" };
const FYERS: SignInProvider = { id: "broker:fyers", label: "Fyers", kind: "broker", start: "/auth/broker/fyers/start" };
const GOOGLE: SignInProvider = { id: "google", label: "Google", kind: "oidc", start: "/auth/google/start" };

describe("M74 Continue with your broker", () => {
  it("tells broker providers apart and gives them initials badges", () => {
    expect(isBrokerProvider(UPSTOX)).toBe(true);
    expect(isBrokerProvider(GOOGLE)).toBe(false);
    expect(brokerInitials(UPSTOX)).toBe("UP");
    expect(brokerInitials({ ...UPSTOX, id: "broker:other", label: "Some Broker" })).toBe("SO");
    expect(providerLabel("broker:zerodha")).toBe("Zerodha");
  });

  it("keeps the server's broker entries from /auth/providers", () => {
    const answer = { providers: [GOOGLE, UPSTOX], google: true };
    expect(providersFrom(answer, false).map((p) => p.id)).toEqual(["google", "broker:upstox"]);
    expect(providersFrom(answer, true)).toEqual([]);          // never in the Android app
  });

  it("shows no broker entry when the server lists no broker", () => {
    expect(brokerOptions([GOOGLE])).toEqual([]);
    expect(renderToStaticMarkup(<BrokerSignIn brokers={[]} />)).toBe("");
  });

  it("renders one 'Continue with your broker' entry, never one button per broker", () => {
    const providers = [GOOGLE, UPSTOX, FYERS];
    const html = renderToStaticMarkup(<SignInOptionsList providers={providers} extra={brokerOptions(providers, "signup")} />);
    expect(html).toContain("Continue with Google");
    expect(html).toContain("Continue with your broker");
    expect(html).not.toContain("Continue with Upstox");
    expect(html).not.toContain("Continue with Fyers");
    expect(html).toContain('aria-expanded="false"');          // the list opens on a tap
  });
});
