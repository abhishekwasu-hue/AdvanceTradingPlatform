import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import type { ExperienceStatus } from "../types";
import { ExperiencePanel } from "./ExperienceCard";
import { gateLine, gateState, nextLevel, reasonOk } from "./experience";
import { tProgression as t } from "./i18n";
import { BEGINNER_STATUS as S } from "./samples.experience";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const render = (s: ExperienceStatus, canTrade = true) =>
  renderToStaticMarkup(<ExperiencePanel status={s} canTrade={canTrade} onOptOut={async () => true} />);

describe("experience helpers (N2-2)", () => {
  it("finds the next level, the gate states and a long-enough reason", () => {
    expect(nextLevel(S)?.key).toBe("INTERMEDIATE");
    expect(nextLevel({ ...S, levels: S.levels.map((l) => ({ ...l, reached: true })) })).toBeNull();
    expect(gateState(S.gates[0])).toBe("optedOut");
    expect(gateState(S.gates[1])).toBe("shut");
    expect(gateState({ ...S.gates[1], allowed_by_level: true })).toBe("allowed");
    expect(gateLine(S.gates[1], t)).toBe("Opens at Advanced");
    expect(gateLine(S.gates[0], t)).toBe("Opted out for today (logged)");
    expect(reasonOk("   short   ", 15)).toBe(false);
    expect(reasonOk("practising futures", 15)).toBe(true);
  });
});

describe("ExperiencePanel (N2-2)", () => {
  it("shows the level, the record, the next level's criteria, the gates and the counts", () => {
    const html = render(S);
    const words = text(html);
    expect(words).toContain("Beginner · current");
    expect(html).toContain('aria-current="step"');
    expect(words).toContain("Your record: 12 closed trades over 8 sessions, largest drawdown 3.20%.");
    expect(words).toContain("Intermediate opens futures:");
    expect(words).toContain("Closed PAPER trades 12 of 30");
    expect(words).toContain("Futures Opted out for today (logged)");
    expect(words).toContain("Naked short options Opens at Advanced");
    expect(words).toContain("Opt-outs: 1 today · 2 in the last 7 days · 5 in all");
    expect(words).toContain("2026-10-12 · Futures at beginner: Practising index futures on paper today");
    expect(words).toContain("Exits are never gated");
  });

  it("offers the opt-out only on a shut gate and only to a trader", () => {
    expect(render(S).match(/Opt out for today/g)?.length).toBe(1);   // the naked gate only (futures is already opted out)
    expect(render(S, false)).not.toContain("Opt out for today");
    const open = render({ ...S, gates: S.gates.map((g) => ({ ...g, allowed_by_level: true, allowed: true })) });
    expect(open).not.toContain("Opt out for today");
    expect(text(open)).toContain("Open at your level");
  });

  it("says when every level is reached", () => {
    expect(text(render({ ...S, level: "ADVANCED", levels: S.levels.map((l) => ({ ...l, reached: true })) }))).toContain("Every level is reached.");
  });
});

import cardSource from "./ExperienceCard.tsx?raw";

describe("UI-28 U1.7 / U1.9: the experience card on the v2 pages", () => {
  it("shows only when the user turned on 'Limit me by my experience level'; sessions live on the ladder only", () => {
    expect(cardSource).toMatch(/loadSwitches\(\)\.then\(\(r\) => setGateOn\(r\.value\.limit_by_experience\)/);
    expect(cardSource).toContain("if (!status || (v2 && !gateOn)) return null;");
    expect(cardSource).not.toMatch(/label: "Sessions", value: r\.sessions/);
    expect(cardSource).toContain('c.key !== "sessions"');
  });
});
