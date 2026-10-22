import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import PwaBanners from "./PwaBanners";
import {
  INSTALL_DONE, INSTALL_IOS, INSTALL_NEEDS_BROWSER, INSTALL_NEEDS_HTTPS, INSTALL_NOT_OFFERED, _setPwaState, initialInstall,
  installBlockReason, installHelp, isIosSafari, isStandalone,
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

describe("D3: install on the production domain", () => {
  const nav = (sw: boolean) => (sw ? { serviceWorker: {} } : {}) as Navigator;

  it("knows what the page can tell before trying: not a secure context, or no service workers", () => {
    expect(installBlockReason({ isSecureContext: false, navigator: nav(true) })).toBe("insecure");
    expect(installBlockReason({ isSecureContext: false, navigator: nav(false) })).toBe("insecure");
    expect(installBlockReason({ isSecureContext: true, navigator: nav(false) })).toBe("no-service-worker");
    expect(installBlockReason({ isSecureContext: true, navigator: nav(true) })).toBeNull();
    // an old browser without isSecureContext is not accused of being insecure
    expect(installBlockReason({ navigator: nav(true) } as unknown as Window)).toBeNull();
  });

  it("the start-up banner is only the iPhone guide; Android and desktop wait for the browser's prompt", () => {
    const base = { dismissed: false, standalone: false, ios: false };
    expect(initialInstall({ ...base, ios: true })).toBe("ios");
    expect(initialInstall(base)).toBeNull();
    expect(initialInstall({ ...base, ios: true, dismissed: true })).toBeNull();
    expect(initialInstall({ ...base, ios: true, standalone: true })).toBeNull();
  });

  it("the install entry always answers: the prompt, or a plain reason - never domain advice", () => {
    const base = { blocked: null, canPrompt: false, ios: false, standalone: false } as const;
    expect(installHelp({ ...base, canPrompt: true })).toEqual({ kind: "prompt" });
    expect(installHelp({ ...base, blocked: "insecure" })).toEqual({ kind: "message", text: INSTALL_NEEDS_HTTPS });
    expect(installHelp({ ...base, ios: true })).toEqual({ kind: "message", text: INSTALL_IOS });
    expect(installHelp({ ...base, blocked: "no-service-worker" })).toEqual({ kind: "message", text: INSTALL_NEEDS_BROWSER });
    expect(installHelp(base)).toEqual({ kind: "message", text: INSTALL_NOT_OFFERED });
    expect(installHelp({ ...base, standalone: true })).toEqual({ kind: "message", text: INSTALL_DONE });
    for (const text of [INSTALL_NEEDS_HTTPS, INSTALL_NEEDS_BROWSER, INSTALL_NOT_OFFERED, INSTALL_IOS, INSTALL_DONE]) {
      expect(text, text).not.toMatch(/domain|owner|certificate/i);
    }
  });

  it("the banners: the install offer with Not now, the iPhone guide, nothing otherwise", () => {
    _setPwaState({ install: "prompt" });
    const prompt = renderToStaticMarkup(<PwaBanners />);
    expect(prompt).toContain("Install app");
    expect(prompt).toContain('aria-label="Not now"');
    _setPwaState({ install: "ios" });
    expect(renderToStaticMarkup(<PwaBanners />)).toContain("Add to Home Screen");
    _setPwaState({ install: null, blocked: null });
    const none = renderToStaticMarkup(<PwaBanners />);
    expect(none).toBe("");
    expect(none).not.toMatch(/domain/i);
  });
});
