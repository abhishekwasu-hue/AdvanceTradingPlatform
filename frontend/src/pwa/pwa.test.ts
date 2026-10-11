import { describe, expect, it } from "vitest";
import { isIosSafari, isStandalone } from "./pwa";

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
