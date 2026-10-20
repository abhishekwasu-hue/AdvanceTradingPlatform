import { describe, expect, it } from "vitest";
// @ts-expect-error - a plain .mjs script without type declarations (test/ is outside the app's tsconfig)
import { findRawColours, isV2File } from "../scripts/check-v2-colours.mjs";

/** UI2-0: the v2 colour lint (scripts/check-v2-colours.mjs, run in the frontend CI job). */
describe("check-v2-colours", () => {
  const found = (s: string) => (findRawColours(s) as { match: string }[]).map((f) => f.match);

  it("flags raw palette hues, with variants, opacity and important", () => {
    expect(found(`<p className="text-sky-300">`)).toEqual(["text-sky-300"]);
    expect(found(`className="hover:bg-violet-500/10 border-amber-400"`)).toEqual(["bg-violet-500/10", "border-amber-400"]);
    expect(found(`"md:!text-emerald-600 ring-rose-500/40 from-slate-950 border-t-red-200"`))
      .toEqual(["!text-emerald-600", "ring-rose-500/40", "from-slate-950", "border-t-red-200"]);
  });

  it("flags white, black and arbitrary colours", () => {
    expect(found(`"text-white bg-black/50"`)).toEqual(["text-white", "bg-black/50"]);
    expect(found(`"bg-[#0ea5e9] text-[rgb(1,2,3)]"`)).toEqual(["bg-[#0ea5e9]", "text-[rgb(1,2,3)]"]);
  });

  it("allows the semantic tokens", () => {
    const ok = `"bg-surface-1 text-fg text-fg-muted border-subtle border-border bg-warn-tint text-warn text-up text-down
      bg-brand text-on-brand hover:bg-brand-strong shadow-raised shadow-card rounded-pill text-t14 p-space-4 ring-brand/40"`;
    expect(found(ok)).toEqual([]);
  });

  it("does not match inside other words", () => {
    expect(found(`const context = "text-sky-300-ish"; // mytext-sky-300`)).toEqual([]);
  });

  it("covers *V2*.tsx files and the src/ui building blocks only", () => {
    expect(isV2File("/r/frontend/src/pages/DashboardV2.tsx")).toBe(true);
    expect(isV2File("/r/frontend/src/components/SidebarV2.tsx")).toBe(true);
    expect(isV2File("/r/frontend/src/ui/BrokerGate.tsx")).toBe(true);
    expect(isV2File("/r/frontend/src/pages/DashboardPage.tsx")).toBe(false);
    expect(isV2File("/r/frontend/src/ui/format.ts")).toBe(false);
  });
});
