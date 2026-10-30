import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// SB-UX SU4 (DEF-0): the builder's stop, target and minimum R:R are the user's own values - blank = off.
vi.mock("../auth/AuthContext", () => {
  const value = () => ({ user: { id: 1, email: "trader@example.com", tenant_id: 1, role: "USER", scopes: [] }, loading: false });
  return { useAuth: value, useOptionalAuth: value };
});

import { defaultCustomStrategyConfig } from "../types";
import StrategyBuilderPage, { exitProblem, ownNumber, ownTargets } from "./StrategyBuilderPage";
import source from "./StrategyBuilderPage.tsx?raw";

describe("Strategy Builder exits (SB-UX SU4)", () => {
  it("prefills no stop, target or minimum R:R", () => {
    const cfg = defaultCustomStrategyConfig();
    expect([cfg.stop_loss_atr_mult, cfg.target_rr, cfg.min_rr]).toEqual([null, null, null]);
  });

  it("reads an empty box as off, never as 0 or a default", () => {
    expect(ownNumber("")).toBeNull();
    expect(ownNumber("  ")).toBeNull();
    expect(ownNumber("abc")).toBeNull();
    expect(ownNumber("1.5")).toBe(1.5);
    expect(ownTargets("", "")).toBeNull();
    expect(ownTargets("2", "")).toEqual([2, 2]);          // one target typed = that one target, nothing invented
    expect(ownTargets("", "3")).toEqual([3, 3]);
    expect(ownTargets("1.5", "2.5")).toEqual([1.5, 2.5]);
  });

  it("says, before Save, that R needs the user's own stop and target", () => {
    const cfg = defaultCustomStrategyConfig();
    expect(exitProblem(cfg)).toBeNull();
    expect(exitProblem({ ...cfg, target_rr: [2, 2] })).toMatch(/needs your stop-loss/);
    expect(exitProblem({ ...cfg, stop_loss_atr_mult: 1, min_rr: 1.5 })).toMatch(/needs your target/);
    expect(exitProblem({ ...cfg, stop_loss_atr_mult: 1, target_rr: [2, 2], min_rr: 1.5 })).toBeNull();
  });

  it("renders the exit boxes empty, marked Off", () => {
    const html = renderToStaticMarkup(<StrategyBuilderPage />);
    expect(html).toContain('id="sb-stop"');
    expect(html).toMatch(/id="sb-stop"[^>]*value=""/);
    expect(html).not.toMatch(/value="1(\.5)?"|value="2"/);
    expect(html.match(/placeholder="Off"/g)?.length).toBe(4);
    expect(html).toContain("A blank box is off");
    // the old prefilled numbers are gone from the source
    expect(source).not.toMatch(/stop_loss_atr_mult: Number\(|target_rr: \[Number\(/);
  });
});
