import { describe, expect, it } from "vitest";
import { attentionRows } from "../dashboard/model";
import { entriesWord, openCaption } from "../trading/model";
import { topBarNotices } from "../shell/TopBarV2";
import { looksLikeSlug } from "../ui/strategyName";
import { closedFlag } from "../shell/pageFlags";
import { DROP_TEXT_DESKTOP, DROP_TEXT_PHONE } from "../chartAnalyze/ChartAnalyzePageV2";

/**
 * The owner's user-friendly pass (DECISIONS 23 Oct): guards that keep the six fixes fixed. Each reads the v2 sources
 * (the same way src/englishOnlyOutsideInterview.test.tsx does) or calls the shared rule directly.
 */
const RAW = import.meta.glob<string>(["../**/*.ts", "../**/*.tsx", "!../**/*.test.ts", "!../**/*.test.tsx", "!../stories/**"],
  { query: "?raw", import: "default", eager: true });
/** Keyed from src: "./trading/RiskV2.tsx". */
const SOURCES: Record<string, string> = Object.fromEntries(Object.entries(RAW).map(([f, src]) => [f.replace(/^\.\.\//, "./"), src]));

/** The v2 user screens and their models (the admin console and today's old pages are out of scope). */
const V2_USER_DIRS = ["./trading/", "./dashboard/", "./signals/", "./fno/", "./paperbot/", "./setup/", "./shell/", "./markets/", "./optionChain/", "./chart/"];
const v2UserFiles = () => Object.entries(SOURCES).filter(([f]) => V2_USER_DIRS.some((d) => f.startsWith(d)));

describe("user-friendly pass: guards", () => {
  it("reads the sources", () => {
    expect(Object.keys(SOURCES).length).toBeGreaterThan(100);
    expect(v2UserFiles().length).toBeGreaterThan(30);
  });

  it("item 1: no 'Verify your email' chip in the v2 top bar; the Needs attention row is the one place", () => {
    expect(SOURCES["./shell/TopBarV2.tsx"]).not.toMatch(/text: "Verify your email"|>\s*Verify your email/);
    const user = { id: 1, email: "a@b.c", tenant_id: 1, role: "OWNER", email_verified: false };
    expect(topBarNotices(null, user)).toEqual([]);
    expect(SOURCES["./dashboard/model.ts"]).toContain('title: "Verify your email"');
  });

  it("item 2: in the paper phase no LIVE count or LIVE word in the counters and rows", () => {
    expect(openCaption({ open: 3, paper: 2, live: 1 }, true)).toBe("2 practice positions");
    expect(openCaption({ open: 1, paper: 1, live: 0 }, true)).toBe("1 practice position");
    expect(openCaption({ open: 3, paper: 2, live: 1 })).toBe("2 paper · 1 live");          // after Phase 0: unchanged
    expect(entriesWord(true)).not.toMatch(/live/i);
    const base = {
      nowMs: Date.parse("2026-10-23T05:00:00Z"), broker: { kind: "connected" } as never, brokerRows: null, pending: [], risk: null,
      exposure: null, unread: [], approvalsPath: "/signals",
    };
    const rows = attentionRows({ ...base, deployments: [{ id: 1, mode: "LIVE", status: "ACTIVE", strategy_id: "orb_15m_5m", symbol: "NIFTY 50", last_error: null } as never],
      status: { maintenance_mode: false, maintenance_message: null, disabled_brokers: ["upstox"] } as never, emailVerified: false, liveHidden: true });
    for (const r of rows) expect(`${r.title} ${r.detail}`).not.toMatch(/\blive\b/i);
    // The pages that printed "paper and live" read the paper-phase switch.
    for (const f of ["./trading/RiskV2.tsx", "./trading/PortfolioV2.tsx", "./trading/PositionsV2.tsx"]) {
      expect(SOURCES[f], f).toMatch(/useLiveHidden\(\)|liveHiddenFrom\(features\)/);
    }
    expect(SOURCES["./shell/AppShellV2.tsx"]).toContain("paperOnly={frontPaperOnly || liveHidden}");
  });

  it("item 3: the role label in the sidebar is shown only for an organisation of more than one member", () => {
    expect(SOURCES["./shell/SidebarV2.tsx"]).toMatch(/showRoleFor\(members\) && <span[^>]*>\{roleLabel\(user\.role\)\}/);
  });

  it("item 4: one money rule - paise only in tickets, fills and P&L detail rows; no hand-built rupees on v2 screens", () => {
    const PAISE_OK = new Set(["./fno/ticketModel.ts", "./trading/model.ts", "./trading/PositionsV2.tsx", "./fno/journal.ts", "./fno/JournalDraftsV2.tsx"]);
    for (const [f, src] of v2UserFiles()) {
      if (src.includes("paise: true")) expect(PAISE_OK.has(f), `${f} shows paise: only tickets, fills and P&L detail rows may`).toBe(true);
      expect(src, `${f} builds rupees by hand; use formatINR (src/ui/format.ts)`).not.toMatch(/₹\$\{[^}]*(toFixed|toLocaleString)/);
      expect(src, `${f} formats rupees with en-IN locale; use formatINR`).not.toMatch(/toLocaleString\("en-IN"[^)]*FractionDigits/);
    }
  });

  it("item 5: every page intro is one short line and never repeats the page's subtitle", () => {
    const intros = Object.entries(SOURCES).flatMap(([f, src]) =>
      [...src.matchAll(/<PageIntro id="([^"]+)"[^>]*?lines=\{\[([\s\S]*?)\]\}/g)].map((m) => ({ file: f, id: m[1], body: m[2], src })));
    expect(intros.length).toBeGreaterThanOrEqual(8);
    const words = (t: string) => t.toLowerCase().replace(/[^a-z0-9' ]+/g, " ").split(/\s+/).filter(Boolean);
    const runs = (t: string, n = 4) => { const w = words(t); return new Set(w.slice(0, Math.max(0, w.length - n + 1)).map((_, i) => w.slice(i, i + n).join(" "))); };
    for (const { file, id, body, src } of intros) {
      if (/\bt\(/.test(body)) continue;                                               // the Copilot's translated lines
      const lines = [...body.matchAll(/"((?:[^"\\]|\\.)*)"/g)].map((m) => m[1]);
      expect(lines, `${id}: one line`).toHaveLength(1);
      expect(lines[0].length, `${id}: short`).toBeLessThanOrEqual(160);
      const subtitles = [...src.matchAll(/subtitle=(?:"([^"]+)"|\{`([^`]+)`\})/g), ...src.matchAll(/<p className="[^"]*text-fg-muted md:mt-space-1[^"]*">([^<]+)</g)]
        .map((m) => m[1] ?? m[2] ?? "");
      const intro = runs(lines[0]);
      for (const sub of subtitles) {
        const shared = [...runs(sub)].filter((r) => intro.has(r));
        expect(shared, `${file} (${id}): the intro repeats the subtitle`).toEqual([]);
      }
    }
  });

  it("item 6: no v2 screen prints a strategy id as it is", () => {
    for (const [f, src] of v2UserFiles()) {
      expect(src, `${f} prints a strategy id; use strategyDisplayName / botDisplayName (src/ui/strategyName.ts)`)
        .not.toMatch(/\$\{[\w.?]*strategy_id\}|>\s*\{[\w.?]+\.strategy_id\}|\{[\w.?]+\.strategy_id\}\s*</);
      expect(src, `${f} turns a strategy id into words by hand`).not.toMatch(/humanise\([^)]*strategy_id/);
    }
    expect(looksLikeSlug("orb_nifty")).toBe(true);
  });

  it("item 7 + A1: Analyze a chart without its engine - out of the nav, 'not available' at its address, the reason for admins only", () => {
    const off = { answered: true, on: (f: string) => f !== "chart_analyze" };
    expect(closedFlag("chart-analyze", off)).toBeNull();                         // A1: the page answers its own closed state
    expect(closedFlag("journal", { answered: true, on: () => false })).toBe("discipline_engine");   // the others: the shell's
    const page = SOURCES["./chartAnalyze/ChartAnalyzePageV2.tsx"];
    expect(page).not.toMatch(/engine not installed|administrator installs/i);
    expect(page).toContain("if (engineMissing && !off) return <EngineUnavailable engine={status.engine} />;");
    expect(page).toContain("if (!on) return <ComingSoon flag={FLAG} />;");
    expect(SOURCES["./shell/navModel.ts"]).toMatch(/"chart-analyze"[\s\S]{0,200}flag: "chart_analyze"/);
  });

  it("item 8: the drop zone's sentences start with a capital", () => {
    for (const text of [DROP_TEXT_DESKTOP, DROP_TEXT_PHONE]) expect(text.charAt(0)).toMatch(/[A-Z]/);
    const page = SOURCES["./chartAnalyze/ChartAnalyzePageV2.tsx"];
    expect(page).not.toMatch(/<\/span>paste a screenshot/);
    // No sentence in a v2 file starts in lower case right after ". " inside JSX text.
    for (const [f, src] of v2UserFiles()) expect(src, f).not.toMatch(/MB` : ""\}\. [a-z]/);
  });
});
