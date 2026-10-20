import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import PwaBanners from "./PwaBanners";
import {
  INSTALL_DONE, INSTALL_IOS, INSTALL_NEEDS_BROWSER, INSTALL_NEEDS_DOMAIN, INSTALL_NOT_OFFERED, _setPwaState, initialInstall,
  installBlockReason, installHelp, isBareIpHost, isCertificateError, isIosSafari, isSecurityError, isStandalone,
  registrationBlocksInstall,
} from "./pwa";

describe("install offer", () => {
  it("recognises Safari on iPhone and iPad, not other browsers", () => {
    const iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1";
    const ipadDesktopMode = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15";
    const chromeIos = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/126.0 Mobile/15E148 Safari/604.1";
    const android = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36";
    expect(isIosSafari(iphone)).toBe(true);
    expect(isIosSafari(ipadDesktopMode, 5)).toBe(true);
    expect(isIosSafari(ipadDesktopMode, 0)).toBe(false);     // a real Mac
    expect(isIosSafari(chromeIos)).toBe(false);
    expect(isIosSafari(android)).toBe(false);
  });

  it("knows when it already runs as the installed app", () => {
    const win = (standalone: boolean, media: boolean) => ({
      navigator: { standalone },
      matchMedia: () => ({ matches: media }),
    }) as unknown as Window;
    expect(isStandalone(win(true, false))).toBe(true);
    expect(isStandalone(win(false, true))).toBe(true);
    expect(isStandalone(win(false, false))).toBe(false);
  });
});

describe("owner mobile brief (Phase A): install explains itself when it cannot work", () => {
  const nav = (sw: boolean) => (sw ? { serviceWorker: {} } : {}) as Navigator;

  it("knows an address that can never install: not a secure context, or no service workers", () => {
    expect(installBlockReason({ isSecureContext: false, navigator: nav(true) })).toBe("insecure");
    expect(installBlockReason({ isSecureContext: false, navigator: nav(false) })).toBe("insecure");
    expect(installBlockReason({ isSecureContext: true, navigator: nav(false) })).toBe("no-service-worker");
    expect(installBlockReason({ isSecureContext: true, navigator: nav(true) })).toBeNull();
    // an old browser without isSecureContext is not accused of being insecure
    expect(installBlockReason({ navigator: nav(true) } as unknown as Window)).toBeNull();
  });

  it("recognises the certificate refusal of a bare IP with an internal certificate", () => {
    const sslError = Object.assign(new Error("Failed to register a ServiceWorker for scope ('https://203.0.113.7/'): An SSL certificate error occurred when fetching the script."), { name: "SecurityError" });
    expect(isCertificateError(sslError)).toBe(true);
    expect(isCertificateError("SecurityError: ... certificate ...")).toBe(true);
    expect(isCertificateError(new Error("Failed to register a ServiceWorker: A bad HTTP response code (404) was received"))).toBe(false);
    expect(isCertificateError(null)).toBe(false);
  });

  it("the banner says why once (dismissable) instead of never appearing; the iPhone guide otherwise", () => {
    const base = { dismissed: false, domainDismissed: false, standalone: false, ios: false };
    expect(initialInstall({ ...base, blocked: "insecure" })).toBe("unavailable");
    expect(initialInstall({ ...base, blocked: "certificate", ios: true })).toBe("unavailable");
    expect(initialInstall({ ...base, blocked: null, ios: true })).toBe("ios");
    expect(initialInstall({ ...base, blocked: "no-service-worker" })).toBeNull();      // explained by the entry, no nag
    expect(initialInstall({ ...base, blocked: "insecure", domainDismissed: true })).toBeNull();
    expect(initialInstall({ ...base, blocked: "insecure", standalone: true })).toBeNull();
    // Closing PW1's install offer earlier does not hide the domain explanation (its own key, review M5) ...
    expect(initialInstall({ ...base, blocked: "certificate", dismissed: true })).toBe("unavailable");
    // ... and closing the domain explanation does not hide the iPhone guide on a good address later.
    expect(initialInstall({ ...base, blocked: null, ios: true, domainDismissed: true })).toBe("ios");
  });

  it("a refused registration means 'needs a domain' for any browser's wording on https, or any failure on a bare IP", () => {
    const https = { protocol: "https:", hostname: "atp.example.com" };
    const named = (name: string, message: string) => Object.assign(new Error(message), { name });
    expect(registrationBlocksInstall(named("SecurityError", "An SSL certificate error occurred when fetching the script."), https)).toBe(true);   // Chrome
    expect(registrationBlocksInstall(named("SecurityError", "The operation is insecure."), https)).toBe(true);                                 // Firefox
    expect(registrationBlocksInstall(named("SecurityError", "Script URL https://x/sw.js fetch resulted in error"), https)).toBe(true);         // Safari
    expect(registrationBlocksInstall("SecurityError: refused", https)).toBe(true);
    expect(registrationBlocksInstall(named("TypeError", "Failed to fetch"), https)).toBe(false);                                               // offline / 404
    expect(registrationBlocksInstall(named("SecurityError", "refused"), { protocol: "http:", hostname: "localhost" })).toBe(false);
    for (const hostname of ["203.0.113.7", "[2001:db8::1]", "2001:db8::1"]) {
      expect(registrationBlocksInstall(named("TypeError", "anything"), { protocol: "https:", hostname }), hostname).toBe(true);
    }
    for (const hostname of ["127.0.0.1", "localhost", "[::1]", "atp.example.com", "1.2.3.example.com"]) expect(isBareIpHost(hostname), hostname).toBe(false);
    expect(isSecurityError({ name: "SecurityError" })).toBe(true);
    expect(isSecurityError(null)).toBe(false);
  });

  it("the install entry always answers: the prompt, or a plain reason - never nothing", () => {
    const base = { blocked: null, canPrompt: false, ios: false, standalone: false } as const;
    expect(installHelp({ ...base, canPrompt: true })).toEqual({ kind: "prompt" });
    expect(installHelp({ ...base, blocked: "insecure" })).toEqual({ kind: "message", text: INSTALL_NEEDS_DOMAIN });
    expect(installHelp({ ...base, blocked: "certificate", canPrompt: true })).toEqual({ kind: "message", text: INSTALL_NEEDS_DOMAIN });
    expect(installHelp({ ...base, ios: true })).toEqual({ kind: "message", text: INSTALL_IOS });
    expect(installHelp({ ...base, blocked: "no-service-worker" })).toEqual({ kind: "message", text: INSTALL_NEEDS_BROWSER });
    expect(installHelp(base)).toEqual({ kind: "message", text: INSTALL_NOT_OFFERED });
    expect(installHelp({ ...base, standalone: true })).toEqual({ kind: "message", text: INSTALL_DONE });
    expect(INSTALL_NEEDS_DOMAIN).toBe("Install needs the app's web address (domain) with a trusted certificate. Ask the owner to set up the domain.");
  });

  it("the banner shows the domain message with a dismiss button", () => {
    _setPwaState({ install: "unavailable", blocked: "certificate" });
    const html = renderToStaticMarkup(<PwaBanners />);
    expect(html).toContain('data-install="unavailable"');
    expect(html).toContain(INSTALL_NEEDS_DOMAIN.replace("'", "&#x27;"));
    expect(html).toContain('aria-label="Dismiss"');
    _setPwaState({ install: null, blocked: null });
    expect(renderToStaticMarkup(<PwaBanners />)).toBe("");
  });
});
