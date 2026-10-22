import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";
import script from "../public/theme-init.js?raw";
import config from "../tailwind.config.js";
import { pullTextSize, setTextSize } from "./appearanceSync";
import { chartFontSize, loadAppearance, textScale } from "./theme";

// Handover item 1: readable text. The tests run without a DOM: stub what theme.ts and the sync read.
function stubBrowser(saved: Record<string, unknown> | null) {
  const store: Record<string, string> = saved ? { atp_appearance: JSON.stringify(saved) } : {};
  const root = { dataset: {} as Record<string, string>, classList: { toggle: () => undefined } };
  vi.stubGlobal("localStorage", { getItem: (k: string) => store[k] ?? null, setItem: (k: string, v: string) => { store[k] = v; },
                                  removeItem: (k: string) => { delete store[k]; } });
  vi.stubGlobal("document", { documentElement: root });
  vi.stubGlobal("window", { dispatchEvent: () => true, matchMedia: () => ({ matches: false }) });
  vi.stubGlobal("CustomEvent", class { constructor(readonly type: string) {} });
  return { store, root };
}

afterEach(() => vi.unstubAllGlobals());

const rem = (v: string) => Number(v.replace("rem", "")) * 16;

describe("the type scale (tailwind.config.js) at the Normal size", () => {
  const sizes = config.theme.extend.fontSize;
  it("is in rem, so the one root scale moves every size", () => {
    for (const [name, [size, { lineHeight }]] of Object.entries(sizes)) {
      expect(size, name).toMatch(/rem$/);
      expect(lineHeight === "1" || lineHeight.endsWith("rem"), name).toBe(true);
    }
  });
  it("never goes below 14 px; body text is 16 px; headings are larger", () => {
    for (const [name, [size]] of Object.entries(sizes)) expect(rem(size), name).toBeGreaterThanOrEqual(14);
    expect(rem(sizes.sm[0])).toBe(16);
    expect(rem(sizes.t14[0])).toBe(16);                      // the v2 body text token
    expect(rem(sizes.t13[0])).toBeGreaterThanOrEqual(14);    // the option chain's cells
    expect(rem(sizes.t24[0])).toBeGreaterThan(rem(sizes.t18[0]));
    expect(rem(sizes.t18[0])).toBeGreaterThan(rem(sizes.t14[0]));
  });
});

describe("no page sets its own tiny size", () => {
  function files(dir: string): string[] {
    return readdirSync(dir).flatMap((n) => {
      const p = join(dir, n);
      return statSync(p).isDirectory() ? files(p) : /\.(tsx?|css)$/.test(n) && !/\.test\./.test(n) ? [p] : [];
    });
  }
  it("has no text-[Npx] class below 14 px", () => {
    const offenders = files(join(__dirname)).flatMap((f) =>
      [...readFileSync(f, "utf8").matchAll(/text-\[(\d+(?:\.\d+)?)px\]/g)].filter((m) => Number(m[1]) < 14).map((m) => `${f}: ${m[0]}`));
    expect(offenders).toEqual([]);
  });
});

describe("the Text size setting", () => {
  it("defaults to Normal and reads a saved size", () => {
    stubBrowser(null);
    expect(loadAppearance().textSize).toBe("normal");
    stubBrowser({ theme: "dark", textSize: "xlarge" });
    expect(loadAppearance().textSize).toBe("xlarge");
    stubBrowser({ textSize: "huge" });
    expect(loadAppearance().textSize).toBe("normal");
  });

  it("sets data-text-size on <html>, and the charts' labels follow it", async () => {
    const { root } = stubBrowser(null);
    expect(chartFontSize()).toBe(12);
    await setTextSize("large", false);
    expect(root.dataset.textSize).toBe("large");
    expect(textScale()).toBe(1.125);
    expect(chartFontSize()).toBe(14);
    await setTextSize("xlarge", false);
    expect(chartFontSize()).toBe(15);
  });

  it("theme-init.js paints the saved size before the app loads", () => {
    const run = (saved: string | null) => {
      const attrs: Record<string, string> = {};
      vi.stubGlobal("document", { documentElement: { setAttribute: (k: string, v: string) => { attrs[k] = v; },
                                                     classList: { add: () => undefined, remove: () => undefined } } });
      vi.stubGlobal("localStorage", { getItem: (k: string) => (k === "atp_appearance" ? saved : null) });
      vi.stubGlobal("window", { matchMedia: () => ({ matches: false }) });
      new Function(script)();
      return attrs["data-text-size"];
    };
    expect(run(JSON.stringify({ textSize: "large" }))).toBe("large");
    expect(run(JSON.stringify({ textSize: "bogus" }))).toBe("normal");
    expect(run(null)).toBe("normal");
  });
});

describe("the Text size is stored per user on the server", () => {
  it("applies the stored size on sign-in", async () => {
    const { root } = stubBrowser(null);
    const fetcher = vi.fn(async () => ({ prefs: { appearance: { text_size: "xlarge" } }, versions: { appearance: 3 } }));
    expect(await pullTextSize(fetcher as never)).toBe("xlarge");
    expect(root.dataset.textSize).toBe("xlarge");
    expect(loadAppearance().textSize).toBe("xlarge");
  });

  it("sends up a size chosen on this device before the server had one", async () => {
    stubBrowser({ textSize: "large" });
    const fetcher = vi.fn(async (path: string) => (path === "/me/prefs"
      ? { prefs: { appearance: { text_size: "normal" } }, versions: { appearance: 0 } } : { version: 1 }));
    expect(await pullTextSize(fetcher as never)).toBe("large");
    expect(fetcher).toHaveBeenCalledWith("/me/prefs/appearance", { method: "PUT", body: JSON.stringify({ text_size: "large" }) });
  });

  it("keeps the device's size when the server cannot be reached", async () => {
    stubBrowser({ textSize: "large" });
    expect(await pullTextSize((async () => { throw new Error("offline"); }) as never)).toBe("large");
  });

  it("a change is saved for the user; a failed save says so but keeps the size here", async () => {
    stubBrowser(null);
    const ok = vi.fn(async () => ({ version: 2 }));
    expect(await setTextSize("large", true, ok as never)).toBe(true);
    expect(ok).toHaveBeenCalledWith("/me/prefs/appearance", { method: "PUT", body: JSON.stringify({ text_size: "large" }) });
    expect(await setTextSize("xlarge", true, (async () => { throw new Error("offline"); }) as never)).toBe(false);
    expect(loadAppearance().textSize).toBe("xlarge");
  });

  it("signed out: nothing is sent", async () => {
    stubBrowser(null);
    const fetcher = vi.fn();
    expect(await setTextSize("large", false, fetcher as never)).toBe(true);
    expect(fetcher).not.toHaveBeenCalled();
  });
});
