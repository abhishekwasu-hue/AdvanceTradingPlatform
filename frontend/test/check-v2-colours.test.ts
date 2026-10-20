import { describe, expect, it } from "vitest";
// @ts-expect-error - a plain .mjs script without type declarations (test/ is outside the app's tsconfig)
import { EXCLUDED, findRawColours, isV2File } from "../scripts/check-v2-colours.mjs";

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

  it("covers src/ui (.ts and .tsx) and *V2.tsx pages and components, with an explicit exclusion list", () => {
    expect(isV2File("/r/frontend/src/pages/DashboardV2.tsx")).toBe(true);
    expect(isV2File("/r/frontend/src/components/SidebarV2.tsx")).toBe(true);
    expect(isV2File("/r/frontend/src/components/shell/BottomNavV2.tsx")).toBe(true);
    expect(isV2File("/r/frontend/src/ui/BrokerGate.tsx")).toBe(true);
    expect(isV2File("/r/frontend/src/ui/format.ts")).toBe(true);                 // L6: helpers too
    expect(isV2File("src/ui/brokerSession.ts")).toBe(true);
    expect(isV2File("/r/frontend/src/ui/format.test.ts")).toBe(false);
    expect(isV2File("/r/frontend/src/ui/brokerRows.fixture.ts")).toBe(false);
    expect(isV2File("/r/frontend/src/pages/DashboardPage.tsx")).toBe(false);
    expect(isV2File("/r/frontend/src/screener/ScreenerV2Panel.tsx")).toBe(false); // only pages/ and components/
    expect(EXCLUDED).toContain("src/components/PlanLimitsV2.tsx");                // plan limits, not UI v2
    expect(isV2File("/r/frontend/src/components/PlanLimitsV2.tsx")).toBe(false);
    expect(isV2File("/r/frontend/src/components/PlanLimitsV2.test.tsx")).toBe(false);
  });

  it("L6: flags literal colours in inline styles and SVG attributes, but not token-based ones", () => {
    expect(found(`<div style={{ color: "#fff", backgroundColor: 'rgba(0, 0, 0, 0.5)' }} />`))
      .toEqual(['color: "#fff', "backgroundColor: 'rgba(0, 0, 0, 0.5)"]);
    expect(found(`<path fill="#0ea5e9" stroke='#123' />`)).toEqual(['fill="#0ea5e9', "stroke='#123"]);
    expect(found(`<circle fill={"hsl(200, 50%, 50%)"} />`)).toEqual(['fill={"hsl(200, 50%, 50%)']);
    expect(found(`style={{ boxShadow: "0 0 0 1px #000" }}`)).toEqual(['boxShadow: "0 0 0 1px #000']);
    expect(found(`<div style={{ background: "rgb(var(--surface-1))", color: "rgb(var(--fg) / 0.8)" }} />`)).toEqual([]);
    expect(found(`<path fill="currentColor" stroke="none" />`)).toEqual([]);
    expect(found(`<a href="#add">jump</a>`)).toEqual([]);                           // an anchor is not a colour
  });

  it("L6: ignores comments, line and block, but not URLs", () => {
    expect(found(`// never text-sky-300 here\nconst a = 1; /* nor bg-violet-500 */`)).toEqual([]);
    expect(found(`{/* fill="#fff" in a JSX comment */}`)).toEqual([]);
    expect(found(`const u = "https://x.example"; const c = "text-rose-500";`)).toEqual(["text-rose-500"]);
    expect((findRawColours(`/* a\n b */\nconst c = "text-rose-500";`) as { line: number }[])[0].line).toBe(3);
  });
});
