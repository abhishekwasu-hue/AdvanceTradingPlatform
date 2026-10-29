import { describe, expect, it } from "vitest";
import { pageFamily, startWebVitals } from "./webVitals";

describe("AUD-G1.5 web vitals", () => {
  it("maps a path to a closed page family (no URL leaves the browser)", () => {
    expect(pageFamily("/")).toBe("today");
    expect(pageFamily("/charts/NIFTY")).toBe("charts");
    expect(pageFamily("/fno/option-chain")).toBe("chain");
    expect(pageFamily("/terminal")).toBe("terminal");
    expect(pageFamily("/settings/account")).toBe("other");
  });

  it("does nothing where PerformanceObserver is missing", () => {
    const stop = startWebVitals({} as unknown as Window, () => { throw new Error("sent"); });
    expect(typeof stop).toBe("function");
  });
});
