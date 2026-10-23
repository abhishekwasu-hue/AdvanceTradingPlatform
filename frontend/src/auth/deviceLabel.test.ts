import { describe, expect, it } from "vitest";
import { deviceLabel } from "./deviceLabel";

describe("deviceLabel (A4)", () => {
  it("names the browser and the system in plain words", () => {
    expect(deviceLabel("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Mobile Safari/537.36")).toBe("Chrome on Android");
    expect(deviceLabel("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")).toBe("Safari on iPhone");
    expect(deviceLabel("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36 Edg/129.0")).toBe("Edge on Windows");
    expect(deviceLabel("Mozilla/5.0 (Macintosh; Intel Mac OS X 14.5; rv:131.0) Gecko/20100101 Firefox/131.0")).toBe("Firefox on Mac");
  });
  it("never shows a raw or empty string", () => {
    expect(deviceLabel(null)).toBe("A browser");
    expect(deviceLabel("")).toBe("A browser");
    expect(deviceLabel("curl/8.0")).toBe("A browser");
  });
});
