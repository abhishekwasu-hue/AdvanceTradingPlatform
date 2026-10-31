import { isValidElement, type ReactElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import pageSource from "../pages/JournalPage.tsx?raw";
import { journalApi, filtersQuery, apiPath } from "./api";
import { EntryEditorV2, entryActions } from "./EntrySheetV2";
import { DEMO_ENTRY, DEMO_LIST, DEMO_SCORE, DEMO_STATS, DEMO_WEEK } from "./journal.fixture";
import { ExportButtonsV2, FiltersV2, JournalListV2 } from "./JournalListV2";
import { JournalStatsV2 } from "./JournalStatsV2";
import { EMPTY_MANUAL, istInstant, lines, manualBody, manualProblems, mostTagged, type ManualForm } from "./journalModel";
import { ManualTradeFormV2, submitManual } from "./ManualTradeV2";
import { WeeklyReviewV2, componentHelp } from "./WeeklyReviewV2";

/**
 * M62 JN-3 / JN-4 / JN-5 (`ui_v2`): the v2 Journal page parts on made-up answers (journal.fixture.ts) - the list (table
 * on a wide screen, cards on a phone), the filters, the entry editor and its saves, the outside-ATP form, the statistics
 * (the backend's hand-worked fixture) and the export buttons. No DOM: the parts are rendered to markup and their
 * handlers called directly.
 */

const phone = vi.hoisted(() => ({ on: false }));
vi.mock("../utils/useMediaQuery", async (orig) => ({ ...(await orig<typeof import("../utils/useMediaQuery")>()), useMediaQuery: () => phone.on }));

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const html = (node: ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
const NO_IMAGE = { image: vi.fn(() => new Promise<Blob>(() => undefined)) };
const BUY_SELL = /\b(buy|sell|buying|selling)\b/i;

/** The elements a stateless part returns (children walked; function components not expanded). */
function elements(node: ReactNode, out: ReactElement[] = []): ReactElement[] {
  if (Array.isArray(node)) { node.forEach((n) => elements(n, out)); return out; }
  if (!isValidElement(node)) return out;
  out.push(node);
  elements((node.props as { children?: ReactNode }).children, out);
  return out;
}
const byProp = (els: ReactElement[], name: string, value: unknown) => els.filter((e) => (e.props as Record<string, unknown>)[name] === value)
  .map((e) => e.props as Record<string, unknown> & { onChange?: (v: unknown) => void; onClick?: () => void });

beforeEach(() => { phone.on = false; });
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe("M62 journal list", () => {
  const list = (over: Partial<Parameters<typeof JournalListV2>[0]> = {}) => html(
    <JournalListV2 state={{ status: "ok", data: DEMO_LIST }} filters={{}} onFilters={() => undefined} onOpen={() => undefined} onAddManual={() => undefined}
      onRetry={() => undefined} onMore={() => undefined} api={NO_IMAGE} exportButtons={<ExportButtonsV2 onExport={() => undefined} busy={null} />} {...over} />);

  it("shows date, symbol, setup, signed ₹ P&L in Indian grouping, R, tags and a chart thumbnail per entry (desktop table)", () => {
    const out = list();
    expect(out).toContain("data-journal-table");
    const t = text(out);
    expect(t).toContain("DEMOA");
    expect(t).toContain("Buyer area");
    expect(t).toContain("+₹1,23,457");                       // whole rupees, Indian grouping, signed
    expect(t).toContain("−₹540");                             // a loss: the true minus sign
    expect(t).toContain("3.25R");
    expect(t).toContain("Early exit");                        // the detector's tag, labelled
    expect(t).toContain("late entry");                        // his own tag, as he wrote it
    expect(t).toContain("Outside ATP");                       // the manual entry, labelled
    expect(t).toContain("Open");                              // an open trade: no P&L invented
    expect((out.match(/data-chart-thumb/g) ?? []).length).toBe(4);
    expect(t).not.toMatch(BUY_SELL);
  });

  it("is one card per entry on a phone", () => {
    phone.on = true;
    const out = list();
    expect(out).toContain("data-journal-cards");
    expect(out).not.toContain("data-journal-table");
    expect((out.match(/data-journal-row=/g) ?? []).length).toBe(4);
    expect(out).toContain("data-filters-toggle");                // the filters fold behind one button; the list comes first
    expect(out).not.toContain("data-journal-filters");
    expect(text(out)).toContain("−0.50R");                       // a negative R keeps its minus sign
  });

  it("has loading, error and empty states (filtered and not)", () => {
    expect(list({ state: { status: "loading" } })).toContain('aria-busy="true"');
    expect(text(list({ state: { status: "error", error: new Error("Cannot reach the server.") } }))).toContain("Try again");
    const none = { ...DEMO_LIST, entries: [], total: 0 };
    expect(text(list({ state: { status: "ok", data: none } }))).toContain("No trades in your journal yet");
    expect(text(list({ state: { status: "ok", data: none }, filters: { outcome: "loss" } }))).toContain("No entries match these filters");
    expect(list({ state: { status: "ok", data: { ...DEMO_LIST, truncated: true } } })).toContain("data-show-more");
  });
});

describe("M62 journal filters", () => {
  it("each control changes one filter; clearing a choice removes it", () => {
    const onChange = vi.fn();
    const els = elements(FiltersV2({ value: { setup: "Buyer area" }, onChange, setups: ["Buyer area", "Seller area"], tags: ["early_exit"] }));
    const combos = els.filter((e) => typeof e.type === "function" && (e.props as { label?: string }).label);
    const pick = (label: string, v: string) => (combos.find((c) => (c.props as { label: string }).label === label)!.props as { onChange: (v: string) => void }).onChange(v);
    pick("Tag", "early_exit");
    expect(onChange).toHaveBeenLastCalledWith({ setup: "Buyer area", tag: "early_exit" });
    pick("Outcome", "loss");
    expect(onChange).toHaveBeenLastCalledWith({ setup: "Buyer area", outcome: "loss" });
    pick("Setup", "");
    expect(onChange).toHaveBeenLastCalledWith({ setup: undefined });
    byProp(els, "data-filter", "date_from")[0].onChange!({ target: { value: "2032-03-01" } });
    expect(onChange).toHaveBeenLastCalledWith({ setup: "Buyer area", date_from: "2032-03-01" });
    byProp(els, "data-filter", "include_manual")[0].onChange!({ target: { checked: false } });
    expect(onChange).toHaveBeenLastCalledWith({ setup: "Buyer area", include_manual: false });
    const opts = (combos[2].props as { options: { label: string }[] }).options.map((o) => o.label);
    expect(opts).toEqual(["All outcomes", "Profit", "Loss", "Flat", "Open"]);
  });

  it("the same filters go to the list, the stats and the export", () => {
    expect(filtersQuery({ setup: "Buyer area", tag: "early_exit", outcome: "win", date_from: "2032-03-01", date_to: "2032-03-31", include_manual: false }))
      .toBe("?setup=Buyer+area&tag=early_exit&outcome=win&date_from=2032-03-01&date_to=2032-03-31&include_manual=false");
    expect(filtersQuery({})).toBe("");
    expect(filtersQuery({ include_manual: true }, { format: "csv" })).toBe("?format=csv");
    expect(apiPath("/api/journal/trades/2/chart?at=entry")).toBe("/journal/trades/2/chart?at=entry");
  });
});

describe("M62 entry editor", () => {
  const editor = () => html(<EntryEditorV2 entry={DEMO_ENTRY} api={journalApi} customTags={["late entry", "fear"]} onChanged={() => undefined} onRemoved={() => undefined} />);

  it("shows the plan read-only, the execution, notes, his tags, the detector's tags, his rules yes / no, charts and images", () => {
    const out = editor();
    const t = text(out);
    for (const s of ["Plan (as recorded)", "Seller area", "15min", "1,012.50", "Invalidation", "Pullback into the seller area", "Execution", "Short",
      "1,005.25", "Stop hit", "Notes", "Save notes", "late entry", "fear", "Early exit", "Suggested", "Confirm", "Dismiss", "My rules",
      "Trend agrees", "Waited for the close", "At entry", "At exit", "Add an image", "1.03R / 0.43R"]) expect(t).toContain(s);
    expect(out).toMatch(/aria-pressed="true"[^>]*data-own-tag="late entry"/);
    expect(out).toMatch(/data-rule="Trend agrees"[\s\S]*?aria-pressed="true"[^>]*data-answer="yes"/);
    expect(out).toContain('data-attachment="7"');
    expect(out).not.toContain("data-remove-manual");               // an ATP trade cannot be removed from the journal
    expect(t).not.toMatch(BUY_SELL);
    const manual = html(<EntryEditorV2 entry={{ ...DEMO_ENTRY, kind: "manual", manual: true }} api={journalApi} customTags={[]} onChanged={() => undefined} onRemoved={() => undefined} />);
    expect(manual).toContain("data-remove-manual");
  });

  it("saves notes, toggles his tags, answers a rule, decides a tag and adds / removes an image", async () => {
    const api = {
      saveNotes: vi.fn(async () => ({ notes: "Waited this time." })),
      saveUserTags: vi.fn(async (_k: string, _i: number, tags: string[]) => ({ user_tags: tags })),
      saveChecklist: vi.fn(async () => ({ checklist: [{ rule: "Size from my settings", followed: true, retired: false }] })),
      decideTag: vi.fn(async () => ({ ...DEMO_ENTRY.tags[0], status: "DISMISSED" as const })),
      attach: vi.fn(async () => ({ id: 8, trade_id: 2, manual_trade_id: null, size_bytes: 10, width: 1, height: 1, created_at: null })),
      removeAttachment: vi.fn(async () => ({ deleted: 7 })),
    };
    const act = entryActions(api, DEMO_ENTRY);
    expect((await act.saveNotes("Waited this time.")).notes).toBe("Waited this time.");
    expect(api.saveNotes).toHaveBeenCalledWith("trade", 2, "Waited this time.");
    expect((await act.toggleTag("fear")).user_tags).toEqual(["late entry", "fear"]);
    expect((await act.toggleTag("late entry")).user_tags).toEqual([]);
    expect((await act.answer("Size from my settings", true)).checklist[0].followed).toBe(true);
    expect(api.saveChecklist).toHaveBeenCalledWith("trade", 2, [{ rule: "Size from my settings", followed: true }]);
    expect((await act.decide(21, "DISMISSED")).tags[0].status).toBe("DISMISSED");
    expect((await act.upload(new File(["x"], "c.png", { type: "image/png" }))).attachments.map((a) => a.id)).toEqual([7, 8]);
    expect((await act.removeImage(7)).attachments).toEqual([]);
  });
});

describe("M62 outside-ATP form", () => {
  const NOW = new Date("2032-03-17T12:00:00+05:30");
  const ready: ManualForm = { ...EMPTY_MANUAL, symbol: " demod ", quantity: "25", entry_time: "2032-03-17T10:15", entry_price: "1,520.5",
    stop_loss: "1540", exit_time: "2032-03-17T11:00", exit_price: "1500", charges: "20", setup: "Seller area", direction: "SHORT" };

  it("needs only symbol, quantity, entry time and price; exit time and price go together; IST times", () => {
    expect(manualProblems(EMPTY_MANUAL, NOW)).toEqual(["Enter the symbol.", "Enter the quantity (more than 0).", "Enter the entry date and time.", "Enter the entry price."]);
    expect(manualProblems(ready, NOW)).toEqual([]);
    expect(manualProblems({ ...ready, exit_price: "" }, NOW)).toContain("Exit time and exit price go together: enter both, or neither.");
    expect(manualProblems({ ...ready, entry_time: "2032-03-18T10:00" }, NOW)).toContain("The entry time is in the future.");
    expect(manualProblems({ ...ready, exit_time: "2032-03-17T09:00" }, NOW)).toContain("The exit time is before the entry time.");
    expect(istInstant("2032-03-17T10:15")).toBe("2032-03-17T10:15:00+05:30");
    expect(manualBody(ready)).toEqual({ symbol: "DEMOD", direction: "SHORT", quantity: 25, entry_time: "2032-03-17T10:15:00+05:30", entry_price: 1520.5,
      stop_loss: 1540, target: null, exit_time: "2032-03-17T11:00:00+05:30", exit_price: 1500, exit_reason: null, charges: 20, setup: "Seller area",
      timeframe: null, notes: null });
  });

  it("sends nothing while a required value is missing, then sends the body once ready", async () => {
    const addManual = vi.fn(async () => ({ ...DEMO_ENTRY, kind: "manual" as const, id: 9 }));
    expect(await submitManual({ addManual }, { ...ready, symbol: "" }, NOW)).toEqual({ problems: ["Enter the symbol."] });
    expect(addManual).not.toHaveBeenCalled();
    const r = await submitManual({ addManual }, ready, NOW);
    expect(r.saved?.id).toBe(9);
    expect(addManual).toHaveBeenCalledWith(manualBody(ready));
    const form = text(html(<ManualTradeFormV2 api={{ addManual }} onSaved={() => undefined} onCancel={() => undefined} />));
    for (const s of ["Long", "Short", "Symbol", "Quantity", "Entry time (IST)", "Entry price", "Exit price", "Add to journal"]) expect(form).toContain(s);
    expect(form).not.toMatch(BUY_SELL);
  });
});

describe("M62 statistics", () => {
  it("renders the hand-worked fixture: win rate, profit factor, expectancy, average win / loss, R, MAE / MFE, then by setup", () => {
    const out = html(<JournalStatsV2 state={{ status: "ok", data: DEMO_STATS }} onRetry={() => undefined} />);
    const t = text(out);
    expect(t).toContain("40.0%");                    // 2 of 5 closed
    expect(t).toContain("2 of 5 closed");
    expect(t).toContain("3.33");                     // 500 / 150
    expect(t).toContain("▲ +₹70");                    // expectancy per closed trade
    expect(t).toContain("▲ +₹350");
    expect(t).toContain("▲ +₹250");
    expect(t).toContain("▼ −₹75");
    expect(t).toContain("▲ +0.25R");
    expect(t).toContain("0.75R / 1.85R");
    expect(t).toContain("1 outside ATP");
    expect(t).toContain("S1");
    expect(t).toContain("+₹67");                      // S1's expectancy 66.67 in whole rupees
    expect(out).toContain('aria-label="Help: How these are worked out"');
    expect(t).not.toMatch(/\b(should|recommend\w*|best|buy|sell)\b/i);
  });

  it("M63 I-7: win rate, profit factor, expectancy and R each carry the glossary (?) once", () => {
    const out = html(<JournalStatsV2 state={{ status: "ok", data: DEMO_STATS }} onRetry={() => undefined} />);
    for (const term of ["Win rate", "Profit factor", "Expectancy", "R"]) {
      expect(out.split(`aria-label="What is ${term}?"`).length - 1, term).toBe(1);
    }
  });

  it("has loading, error and empty states", () => {
    expect(html(<JournalStatsV2 state={{ status: "loading" }} onRetry={() => undefined} />)).toContain('aria-busy="true"');
    expect(text(html(<JournalStatsV2 state={{ status: "error", error: new Error("x") }} onRetry={() => undefined} />))).toContain("Try again");
    const empty = { ...DEMO_STATS, totals: { ...DEMO_STATS.totals, trades: 0, outside_atp: 0 } };
    expect(text(html(<JournalStatsV2 state={{ status: "ok", data: empty }} onRetry={() => undefined} />))).toContain("No trades match these filters");
  });
});

describe("M62 weekly review", () => {
  it("shows the discipline score with a (?) on each component, what he tagged most and the week's trades", () => {
    const out = html(<WeeklyReviewV2 state={{ status: "ok", data: DEMO_WEEK }} week="2032-W11" onWeek={() => undefined} onRetry={() => undefined} onOpen={() => undefined} />);
    const t = text(out);
    expect(t).toContain("78");
    expect((out.match(/aria-label="Help: /g) ?? []).length).toBe(DEMO_SCORE.components.length);
    expect(t).toContain("What you tagged most");
    expect((out.match(/data-week-row=/g) ?? []).length).toBe(3);   // 2 ATP trades + 1 outside ATP
    expect(t).toContain("Outside ATP");
    expect(componentHelp(DEMO_SCORE.components[1])).toContain("Early exit (closed by hand between the stop and the first target)");
    expect(componentHelp(DEMO_SCORE.components[1])).toContain("Weight 25% of the score");
  });

  it("counts his tags and the detector's that were not dismissed", () => {
    expect(mostTagged([...DEMO_WEEK.entries, ...DEMO_WEEK.manual_entries!])).toEqual([
      { tag: "late entry", label: "late entry", count: 3 }, { tag: "early_exit", label: "Early exit", count: 2 },
    ]);
    expect(lines(" a \n\n b  c \na\n")).toEqual(["a", "b c"]);
  });
});

describe("M62 export", () => {
  it("the buttons ask for CSV and PDF", () => {
    const onExport = vi.fn();
    const els = elements(ExportButtonsV2({ onExport, busy: null }));
    byProp(els, "data-export", "csv")[0].onClick!();
    byProp(els, "data-export", "pdf")[0].onClick!();
    expect(onExport.mock.calls).toEqual([["csv"], ["pdf"]]);
    expect(text(html(<ExportButtonsV2 onExport={onExport} busy="pdf" />))).toContain("Preparing…");
  });

  it("downloads the file with the list's filters and the server's file name", async () => {
    const fetchMock = vi.fn(async () => new Response("a,b\n", { status: 200, headers: { "content-disposition": 'attachment; filename="journal-all_today.csv"' } }));
    vi.stubGlobal("fetch", fetchMock);
    const r = await journalApi.exportFile("csv", { setup: "Buyer area" });
    expect(r.filename).toBe("journal-all_today.csv");
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toMatch(/\/journal\/export\?format=csv&setup=Buyer\+area$/);
  });
});

describe("M62 page wiring", () => {
  it("the v2 page is behind ui_v2, the classic page stays as it was", () => {
    expect(pageSource).toMatch(/if \(features\.on\("ui_v2"\)\) \{\s*return <JournalPageV2/);
    expect(pageSource).toContain("return <JournalClassic {...props} />;");
  });
});
