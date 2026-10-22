import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";
import IdeaPanel, { changeLine, EDIT_EXAMPLES, versionLabel, type IdeaVersion } from "./IdeaPanel";
import ScanAlertPanel, { DONE, ruleLine, whenLine } from "./ScanAlertPanel";
import ScanVersionsPanel from "./ScanVersionsPanel";
import type { IdeaResponse, ScanAlertRule } from "./api";
import contract from "./builderTexts.json";
import { newStage, type Registry, type Stage } from "./model";
import { addVersion, changes, MAX_VERSIONS, readStore, readsOf, savedAt, snapshot, VERSIONS_KEY, writeStore, type VersionStore } from "./versions";
import { check } from "../signals/language";

/** Handover 11j steps 6-7: edits by chat with versions, the saved scan's versions, "Tell me when it matches". Made-up data. */
const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, '"');
const reg = contract.registry as unknown as Registry;

const BASE: IdeaResponse = {
  ok: true, text: 'IsFnO() AND Trend() == "UPTREND"', base_tf: "1d", stages: [], not_supported: [],
  parts: [
    { rule: "fno", phrase: "F&O", condition: "IsFnO()", reads: "the symbol is in the F&O segment", assumptions: [], ok: true, problems: [] },
    { rule: "trend_up", phrase: "uptrend", condition: 'Trend() == "UPTREND"', reads: "the market-structure trend is up", assumptions: [], ok: true, problems: [] },
  ],
  disclaimer: "A draft scan from your idea: check each block, edit it, then run it. Matches are filters passed, not advice.",
  edits: [],
};
const EDITED: IdeaResponse = {
  ...BASE, text: "IsFnO()", parts: BASE.parts.slice(0, 1),
  edits: [{ edit: "drop the trend", kind: "removed", message: "Removed: the market-structure trend is up.", changed: true,
    removed: ["the market-structure trend is up"], added: [] }],
};
const VERSIONS: IdeaVersion[] = [
  { text: "F&O stocks in an uptrend", edits: [], result: BASE },
  { text: "F&O stocks in an uptrend", edits: ["drop the trend"], result: EDITED },
];
const API = { idea: async () => BASE };

describe("edits by chat on a scan from an idea", () => {
  it("offers the edit box with examples once the idea is read", () => {
    const t = text(renderToStaticMarkup(<IdeaPanel baseTf="1d" onOpen={() => undefined} api={API} initialText="F&O stocks in an uptrend" initialResult={BASE} />));
    expect(t).toContain("Change it in plain words");
    for (const x of EDIT_EXAMPLES) expect(t).toContain(x);
    expect(t).not.toContain("Versions");
    expect(t).toContain("Open as blocks");
  });

  it("lists the versions, says what changed from the one before and opens the one shown", () => {
    const html = renderToStaticMarkup(<IdeaPanel baseTf="1d" onOpen={() => undefined} api={API} initialText="F&O stocks in an uptrend" initialVersions={VERSIONS} />);
    const t = text(html);
    expect(t).toContain("Version 1 · your idea");
    expect(t).toContain("Version 2 · drop the trend");
    expect(t).toContain("What changed from version 1: Removed: the market-structure trend is up.");
    expect(t).toContain("Open version 2 as blocks");
    expect(html).toContain('aria-pressed="true"');
    expect(versionLabel(VERSIONS[0], 0)).toBe("Version 1 · your idea");
    expect(changeLine(VERSIONS[0])).toBeNull();
    expect(changeLine(VERSIONS[1])).toBe("Removed: the market-structure trend is up.");
  });

  it("the new words are in the signal language", () => {
    for (const line of ["Change it in plain words", "Apply", "Versions", ...EDIT_EXAMPLES.map((x) => x.replace(/\d+/g, ""))]) {
      expect(check(line, "signal_public")).toEqual([]);
    }
  });
});

function stages(...values: number[]): Stage[] {
  return values.map((v) => newStage(reg, "RSI", { kind: "value", value: v }, ">"));
}

describe("the saved scan's versions (on this device)", () => {
  const t0 = new Date("2026-10-22T08:35:00Z");
  const t1 = new Date("2026-10-22T09:05:00Z");

  it("a save that changes the scan adds a version; the same conditions only rename the latest", () => {
    let store: VersionStore = {};
    store = addVersion(store, 7, snapshot(reg, "My scan", "1d", stages(50), t0));
    store = addVersion(store, 7, snapshot(reg, "My scan, renamed", "1d", stages(50), t1));
    expect(store["7"]).toHaveLength(1);
    expect(store["7"][0].name).toBe("My scan, renamed");
    store = addVersion(store, 7, snapshot(reg, "My scan", "1h", stages(50, 60), t1));
    expect(store["7"]).toHaveLength(2);
    for (let i = 0; i < 30; i += 1) store = addVersion(store, 7, snapshot(reg, "x", "1d", stages(i), t1));
    expect(store["7"]).toHaveLength(MAX_VERSIONS);
  });

  it("says what changed in plain lines", () => {
    const a = snapshot(reg, "A", "1d", stages(50), t0);
    const b = snapshot(reg, "B", "1h", stages(60), t1);
    expect(changes(undefined, a)).toEqual(["First saved version."]);
    const lines = changes(a, b);
    expect(lines[0]).toMatch(/^Added: RSI/);
    expect(lines[1]).toMatch(/^Removed: RSI/);
    expect(lines).toContain("Timeframe: 1d to 1h");
    expect(lines).toContain("Name: “A” to “B”");
    expect(changes(a, { ...a })).toEqual(["No change in the conditions."]);
    expect(savedAt(t0.toISOString())).toMatch(/22 Oct/);
    expect(savedAt(t0.toISOString())).toContain("14:05");                 // India time
  });

  it("storage that fails or holds junk is no history, never an error", () => {
    const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("full"); } };
    expect(readStore(broken)).toEqual({});
    expect(() => writeStore({}, broken)).not.toThrow();
    expect(readStore({ getItem: () => "[1,2]" })).toEqual({});
    expect(readStore({ getItem: () => JSON.stringify({ 7: [{ at: 1 }, snapshot(reg, "A", "1d", stages(50), t0)] }) })["7"]).toHaveLength(1);
    let saved = "";
    writeStore({ 1: [] }, { setItem: (k, v) => { expect(k).toBe(VERSIONS_KEY); saved = v; } });
    expect(saved).toBe('{"1":[]}');
  });

  it("the versions list shows the newest first and opens an older one", () => {
    const list = [snapshot(reg, "A", "1d", stages(50), t0), snapshot(reg, "A", "1d", stages(60), t1)];
    const t = text(renderToStaticMarkup(<ScanVersionsPanel versions={list} onOpen={() => undefined} />));
    expect(t.indexOf("Version 2")).toBeLessThan(t.indexOf("Version 1"));
    expect(t).toContain("(latest)");
    expect(t).toContain("Open this version");
    expect(t).toContain("Kept on this device.");
    expect(text(renderToStaticMarkup(<ScanVersionsPanel versions={[]} onOpen={() => undefined} />))).toContain("Save the scan to start its history.");
  });
});

describe("Tell me when it matches", () => {
  const rule: ScanAlertRule = { id: 3, name: "My scan", kind: "screen", screen_id: 9, base_tf: "1d", status: "active", symbols: ["A", "B"], fire_on: "bar_close" };
  const api = { rules: async () => [rule], create: async () => rule, setStatus: async () => rule };
  const render = (props: Partial<Parameters<typeof ScanAlertPanel>[0]> = {}) => text(renderToStaticMarkup(
    <MemoryRouter><ScanAlertPanel name="My scan" symbols={["A", "B"]} scanTf="1d" screenId={9} save={async () => 9} api={api} initialRules={[rule]} {...props} /></MemoryRouter>));

  it("says what it will check, where it tells him, and lists the alerts already on this scan", () => {
    const t = render();
    expect(t).toContain("ATP checks each time a daily bar closes for “My scan” on your 2 symbols, and tells you when one matches.");
    expect(t).toContain("When the bar closes");
    expect(t).not.toContain("While the bar is forming");
    expect(t).toContain("Settings › Alerts");
    expect(t).toContain("Turn on the alert");
    expect(t).toContain("Alerts on this scan");
    expect(t).toContain("On · checks each time a daily bar closes · 2 symbols");
    expect(t).toContain("it never places or changes an order");
  });

  it("needs symbols; offers the forming bar only with its flag; shows no other scan's alerts", () => {
    expect(render({ symbols: [] })).toContain("Add the symbols to watch in the Universe column first");
    expect(render({ intrabar: true })).toContain("While the bar is forming");
    expect(render({ screenId: 10 })).not.toContain("Alerts on this scan");
    expect(render({ screenId: undefined })).not.toContain("Alerts on this scan");
    expect(whenLine("15m", "intrabar")).toBe("checks while each 15-minute bar is forming");
    expect(ruleLine({ ...rule, status: "paused", symbols: ["A"] })).toBe("Paused · checks each time a daily bar closes · 1 symbol");
  });

  it("its words are in the signal language", () => {
    for (const line of [DONE, "Turn on the alert", "When the bar closes", "While the bar is forming", whenLine("1d", "bar_close")]) {
      expect(check(line, "signal_public")).toEqual([]);
    }
  });
});

describe("a scan from an idea keeps its plain words in the versions", () => {
  it("maps each block of a part (and each AND piece) to what the part checks", () => {
    const reads = readsOf([
      { condition: "IsFnO()", reads: "the symbol is in the F&O segment", ok: true },
      { condition: "RSI() >= 35 AND RSI() <= 45", reads: "RSI is between 35 and 45", ok: true },
      { condition: "(A() OR B())", reads: "either", ok: true },
      { condition: 'NearZone("demand")', reads: "at a buyer area", ok: false },
    ]);
    expect(reads["RSI() >= 35"]).toBe("RSI is between 35 and 45");
    expect(reads["RSI() <= 45"]).toBe("RSI is between 35 and 45");
    expect(reads["(A() OR B())"]).toBe("either");
    expect(reads['NearZone("demand")']).toBeUndefined();
    const filter = (fn: string): Stage => ({ id: fn, kind: "filter", call: { fn, params: {}, tf: null }, enabled: true });
    const a = snapshot(reg, "A", "1d", [filter("IsFnO")], new Date("2026-10-22T08:35:00Z"), reads);
    expect(a.summaries).toEqual(["the symbol is in the F&O segment"]);
    expect(changes(a, snapshot(reg, "A", "1d", [], new Date("2026-10-22T08:35:00Z"), reads))).toEqual(["Removed: the symbol is in the F&O segment"]);
  });
});
