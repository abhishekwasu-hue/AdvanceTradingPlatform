import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { AskAiView, PlainAnswer, saveAnalysis } from "./AskAiV2";
import askViewSource from "./AskAiV2.tsx?raw";
import { AskChart, ResultTable } from "./AskResultV2";
import resultSource from "./AskResultV2.tsx?raw";
import {
  MAX_SAVED, askAi, cleanName, defaultName, errorSentence, evaluateSpec, formatCell, formatStrike, loadSaved, newId, saveSaved,
  shownResult, withAdded, withDeleted, withRenamed, yScale, type AnalysisSpec, type AskResponse, type SavedState,
} from "./askAi";
import askSource from "./askAi.ts?raw";
import { MyAnalysesView, nextId } from "./MyAnalysesV2";
import mySource from "./MyAnalysesV2.tsx?raw";

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const noop = () => undefined;
// The platform's advice words (backend app/option_chain/ask_spec.py _ADVICE and the Copilot's banned list).
const ADVICE = /\b(recommend\w*|advice|advise|should|must|buy\w*|bought|sell\w*|sold|guarantee\w*|best|sure[- ]?shot|risk[- ]?free|target\w*|stop[- ]?loss|profit\w*)\b/i;
const DISCLAIMER = "Educational illustration only - not investment advice or a recommendation to trade. Check it against your own plan and risk limits.";

const SPEC: AnalysisSpec = {
  version: 1, title: "CE-PE OI near ATM", window: 2,
  columns: [{ label: "CE OI", expr: "ce_oi" }, { label: "PE OI", expr: "pe_oi" }, { label: "CE-PE OI", expr: "ce_oi - pe_oi" }],
  filters: [], chart: { type: "bar", x: "strike", y: ["CE-PE OI"] },
};
const DESCRIPTION = [
  "Strikes: the ATM strike (the listed strike nearest the underlying price) and 2 listed strikes on each side of it.",
  "Column \"CE-PE OI\": call OI minus put OI.",
];
const ROWS = [
  { strike: 21950, atm_offset: -2, cells: [200, 700, -500] }, { strike: 22000, atm_offset: -1, cells: [300, 650, -350] },
  { strike: 22050, atm_offset: 0, cells: [400, 300, 100] }, { strike: 22100, atm_offset: 1, cells: [500, 100, 400] },
  { strike: 22150, atm_offset: 2, cells: [600, 50, 550] },
];
const RESULT = {
  state: "ok" as const, message: null, spot: 22040, atm_strike: 22050, window: 2, rows_in_window: 5, rows_kept: 5, history: [],
  columns: [{ key: "strike", label: "Strike" }, { key: "c0", label: "CE OI" }, { key: "c1", label: "PE OI" }, { key: "c2", label: "CE-PE OI" }],
  rows: ROWS,
  chart: { type: "bar" as const, x_label: "Strike", series: [{ label: "CE-PE OI", points: ROWS.map((r) => ({ x: r.strike, y: r.cells[2] })) }] },
};
const OK: AskResponse = {
  symbol: "NIFTY", expiry: "2026-10-27", state: "ok", message: null, spec: SPEC, description: DESCRIPTION, result: RESULT, closest: null,
  data: { spot: 22040, data_as_of: "2026-10-21T05:30:00+00:00", snapshots: 3, history_note: null }, disclaimer: DISCLAIMER,
};
const UNSUPPORTED: AskResponse = {
  ...OK, state: "unsupported", message: "News sentiment is not part of the option chain.", spec: null, description: [], result: null,
  closest: { spec: SPEC, description: DESCRIPTION, result: RESULT, note: "The closest analysis that can be expressed is shown below; it uses no AI to run." },
};
const QUOTA: AskResponse = {
  ...OK, state: "quota", spec: null, description: [], result: null, data: null,
  message: "Your plan's AI budget for this month is used up, so Ask AI is paused until the 1st. Saved analyses still update; they use no AI.",
};

function view(response: AskResponse | null, extra: { error?: string | null; saveNote?: string | null; question?: string; symbol?: string } = {}) {
  return renderToStaticMarkup(
    <AskAiView symbol={extra.symbol ?? "NIFTY"} question={extra.question ?? ""} onQuestion={noop} onSubmit={noop} busy={false}
      response={response} error={extra.error ?? null} onSave={noop} saving={false} saveNote={extra.saveNote ?? null} onExample={noop} />,
  );
}

afterEach(() => { vi.restoreAllMocks(); });

describe("OC3 requests", () => {
  it("asks with POST /api/option-chain/ask - symbol, expiry and the question", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(OK), { status: 200 }));
    await askAi(" nifty ", "2026-10-27", "  strikes where OI rose more than 20% today ");
    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toMatch(/\/option-chain\/ask$/);
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({ symbol: "NIFTY", expiry: "2026-10-27", question: "strikes where OI rose more than 20% today" });
  });

  it("evaluates a saved spec with POST /api/option-chain/eval and sends no question (no AI)", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify(OK), { status: 200 }));
    await evaluateSpec("NIFTY", null, SPEC);
    const [url, init] = spy.mock.calls[0];
    expect(String(url)).toMatch(/\/option-chain\/eval$/);
    expect(JSON.parse(String(init?.body))).toEqual({ symbol: "NIFTY", spec: SPEC });
  });

  it("a refused request shows the server's plain sentence (rate limit), a 422 a sentence of its own", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      detail: "Too many AI requests: your limit for this minute is used up. Please try again shortly." }), { status: 429 }));
    const err = await askAi("NIFTY", null, "anything at all").catch((e) => e);
    expect(errorSentence(err)).toBe("Too many AI requests: your limit for this minute is used up. Please try again shortly.");
    expect(errorSentence(new ApiError(422, "x"))).toBe("That analysis could not be read. Ask again in other words.");
  });
});

describe("OC3 Ask AI box", () => {
  it("shows the box with examples before anything is asked", () => {
    const words = text(view(null));
    expect(words).toContain("Ask AI");
    expect(words).toContain("Show the CE-PE OI difference for ATM +- 5 strikes as a table");
    expect(words).toContain("Strikes where OI rose more than 20% today");
    expect(view(null, { symbol: "" })).toContain("Pick a symbol first.");
  });

  it("shows the server's table, the chart, How this was calculated and the disclaimer under a result", () => {
    const html = view(OK);
    const words = text(html);
    expect(html).toContain('data-oc-ask="ok"');
    expect(html).toMatch(/<table/);
    expect(html).toMatch(/data-chart="bar"><svg[^>]*role="img"/);
    for (const label of ["Strike", "CE OI", "PE OI", "CE-PE OI"]) expect(words).toContain(label);
    expect(words).toContain("−500");                         // a true minus sign, the server's number
    expect(words).toContain("22050 ATM");
    expect(html).toMatch(/<details[^>]*data-oc-ask-how/);
    expect(words).toContain("How this was calculated");
    for (const line of DESCRIPTION) expect(words).toContain(line);
    expect(words).toContain("the AI only wrote the analysis");
    expect(words).toContain(DISCLAIMER);
    expect(words).toContain("Save as My analysis");
    expect(words).toContain("ATM 22050 · spot 22,040 · 5 of 5 strikes");
  });

  it("puts every number from the server in the table and invents none", () => {
    const html = renderToStaticMarkup(<ResultTable result={RESULT} />);
    const cells = [...html.matchAll(/<td[^>]*>([^<]*)<\/td>/g)].map((m) => m[1]);
    expect(cells).toEqual(ROWS.flatMap((r) => r.cells.map((v) => formatCell(v))));
    expect(formatCell(null)).toBe("–");
    expect(formatCell(-1234.5)).toBe("−1,234.5");
    expect(formatCell(33.333333)).toBe("33.33");
    expect(formatStrike(22000)).toBe("22000");
    expect(formatStrike(122.5)).toBe("122.5");
  });

  it("an unsupported request: one sentence and the closest analysis, with its own How and the disclaimer, no Save", () => {
    const html = view(UNSUPPORTED);
    const words = text(html);
    expect(html).toContain('data-oc-ask-plain="unsupported"');
    expect(words).toContain("News sentiment is not part of the option chain.");
    expect(words).toContain("The closest analysis that can be expressed is shown below");
    expect(words).toContain("How this was calculated");
    expect(words).toContain(DISCLAIMER);
    expect(words).not.toContain("Save as My analysis");
    expect(shownResult(UNSUPPORTED)).toBeNull();
  });

  it("quota exhaustion shows the plain message and no table", () => {
    const html = view(QUOTA);
    const words = text(html);
    expect(words).toContain("Your plan's AI budget for this month is used up, so Ask AI is paused until the 1st.");
    expect(html).not.toContain("<table");
    expect(words).not.toContain("Save as My analysis");
  });

  it("a failed call shows its sentence", () => {
    expect(text(view(null, { error: "Too many AI requests: your limit for this minute is used up. Please try again shortly." })))
      .toContain("Too many AI requests");
  });

  it("the chart scale covers every value and zero for bars; nothing to draw is no chart", () => {
    expect(yScale(RESULT.chart)).toEqual({ min: -500, max: 550 });
    const empty = { ...RESULT.chart, series: [{ label: "x", points: [{ x: 1, y: null }] }] };
    expect(yScale(empty)).toBeNull();
    expect(renderToStaticMarkup(<AskChart chart={empty} title="t" />)).toBe("");
  });
});

describe("OC3 Save as My analysis", () => {
  const empty: SavedState = { items: {}, version: 0 };

  it("adds the spec on the version it read, named after the analysis", async () => {
    const save = vi.fn().mockResolvedValue({ ok: true, state: { items: {}, version: 1 } });
    const note = await saveAnalysis(SPEC, async () => empty, save);
    expect(note).toContain("Saved as \"CE-PE OI near ATM\" under My analyses");
    expect(note).toContain("uses no AI");
    const [items, version] = save.mock.calls[0];
    expect(version).toBe(0);
    const [only] = Object.values(items as Record<string, { name: string; spec: AnalysisSpec }>);
    expect(only.name).toBe("CE-PE OI near ATM");
    expect(only.spec).toEqual(SPEC);
  });

  it("another device's newer list: adds to that list once, on its version", async () => {
    const theirs: SavedState = { items: { other001: { name: "Theirs", spec: SPEC } }, version: 4 };
    const save = vi.fn()
      .mockResolvedValueOnce({ ok: false, conflict: theirs, message: "changed" })
      .mockResolvedValueOnce({ ok: true, state: theirs });
    await saveAnalysis(SPEC, async () => empty, save);
    expect(save.mock.calls[1][1]).toBe(4);
    expect(Object.keys(save.mock.calls[1][0])).toContain("other001");
  });

  it("at the most a user can save, it asks to delete one first and saves nothing", async () => {
    const full: SavedState = { items: Object.fromEntries(Array.from({ length: MAX_SAVED }, (_, i) => [`id${i}`, { name: `A${i}`, spec: SPEC }])), version: 2 };
    const save = vi.fn();
    expect(await saveAnalysis(SPEC, async () => full, save)).toContain("Delete one under My analyses first");
    expect(save).not.toHaveBeenCalled();
  });

  it("reads and writes the oc_analyses preferences namespace with a version; a 409 returns the newer list", async () => {
    const spy = vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(new Response(JSON.stringify({
      prefs: { oc_analyses: { items: { abcd1234: { name: "Mine", spec: SPEC } } } }, versions: { oc_analyses: 3 } }), { status: 200 }));
    expect(await loadSaved()).toEqual({ items: { abcd1234: { name: "Mine", spec: SPEC } }, version: 3 });
    spy.mockResolvedValueOnce(new Response(JSON.stringify({ namespace: "oc_analyses", value: { items: {} }, version: 4 }), { status: 200 }));
    expect(await saveSaved({}, 3)).toEqual({ ok: true, state: { items: {}, version: 4 } });
    const [url, init] = spy.mock.calls[1];
    expect(String(url)).toMatch(/\/me\/prefs\/oc_analyses\?version=3$/);
    expect(init?.method).toBe("PUT");
    expect(JSON.parse(String(init?.body))).toEqual({ items: {} });
    spy.mockResolvedValueOnce(new Response(JSON.stringify({ detail: { message: "changed", version: 5, current: { items: { x0000001: { name: "New", spec: SPEC } } } } }), { status: 409 }));
    const outcome = await saveSaved({}, 3);
    expect(outcome.ok).toBe(false);
    if (!outcome.ok) expect(outcome.conflict).toEqual({ items: { x0000001: { name: "New", spec: SPEC } }, version: 5 });
  });

  it("names, ids, rename and delete are pure", () => {
    expect(cleanName("  My   OI view ")).toBe("My OI view");
    expect(cleanName("<b>x</b>")).toBeNull();
    expect(cleanName("")).toBeNull();
    expect(defaultName({ ...SPEC, title: "CE-PE OI near ATM" })).toBe("CE-PE OI near ATM");
    let n = 0;
    const id = newId({ aaaaaaaa: { name: "x", spec: SPEC } }, () => (n++ < 8 ? 0 : 0.5));
    expect(id).toMatch(/^[a-z0-9]{8}$/);
    expect(id).not.toBe("aaaaaaaa");
    const items = withAdded({}, "abcd1234", { name: "One", spec: SPEC });
    expect(withRenamed(items, "abcd1234", "Two").abcd1234.name).toBe("Two");
    expect(withRenamed(items, "nope0000", "Two")).toBe(items);
    expect(withDeleted(items, "abcd1234")).toEqual({});
  });
});

describe("OC3 My analyses", () => {
  const saved: SavedState = { items: { abcd1234: { name: "OI jump", spec: SPEC }, efgh5678: { name: "Near ATM", spec: SPEC } }, version: 2 };
  const my = (response: AskResponse | null, state: SavedState | null = saved, current: string | null = "abcd1234") =>
    renderToStaticMarkup(<MyAnalysesView saved={state} current={current} onPick={noop} response={response} error={null} note={null}
      onRename={noop} onDelete={noop} />);

  it("shows each saved analysis as its own tab, the selected one's table and the disclaimer", () => {
    const html = my(OK);
    const words = text(html);
    expect(html.match(/role="tab"/g)).toHaveLength(2);
    expect(html.match(/aria-selected="true"/g)).toHaveLength(1);
    expect(html).toMatch(/id="oc-my-tab-abcd1234"[^>]*tabindex="0"/);
    expect(words).toContain("OI jump");
    expect(words).toContain("Near ATM");
    expect(words).toContain("Rename");
    expect(words).toContain("Delete");
    expect(html).toContain("<table");
    expect(words).toContain("How this was calculated");
    expect(words).toContain(DISCLAIMER);
  });

  it("no saved analyses: says how to make one; a plain state shows its sentence", () => {
    expect(text(my(null, { items: {}, version: 0 }, null))).toContain("No saved analyses yet. Ask AI about this chain and choose Save as My analysis.");
    const plain = text(my({ ...QUOTA, state: "no_broker", message: "Connect your broker and log in to it today to run an analysis." }));
    expect(plain).toContain("Connect your broker");
  });

  it("arrow keys move between the tabs and wrap", () => {
    const ids = ["a", "b", "c"];
    expect(nextId(ids, "a", "ArrowRight")).toBe("b");
    expect(nextId(ids, "a", "ArrowLeft")).toBe("c");
    expect(nextId(ids, "b", "End")).toBe("c");
    expect(nextId(ids, "b", "Home")).toBe("a");
    expect(nextId(ids, "b", "Enter")).toBeNull();
  });
});

describe("OC3 words", () => {
  it("no advice and no buy / sell words in what the components show or say", () => {
    for (const html of [view(null), view(OK), view(UNSUPPORTED), view(QUOTA),
      renderToStaticMarkup(<PlainAnswer response={UNSUPPORTED} />)]) {
      const words = text(html).replace(DISCLAIMER, "");          // the disclaimer names advice to say it is not advice
      expect(words).not.toMatch(ADVICE);
    }
    for (const source of [askSource, askViewSource, resultSource, mySource]) {
      const literals = [...source.matchAll(/"([^"\n]*)"|`([^`\n]*)`|>([^<>{}\n]+)</g)].map((m) => m[1] ?? m[2] ?? m[3] ?? "");
      expect(literals.filter((l) => ADVICE.test(l))).toEqual([]);
    }
  });
});
