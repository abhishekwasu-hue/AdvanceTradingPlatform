import { type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { journalApi } from "./api";
import { EntryEditorV2, sheetTitle } from "./EntrySheetV2";
import { DEMO_ENTRY, DEMO_LIST } from "./journal.fixture";
import { ExportButtonsV2, JournalListV2 } from "./JournalListV2";
import { syncText } from "./JournalPageV2";
import type { JournalEntryFull, JournalListRow } from "./types";

/**
 * P2-8 J1 (`ui_v2` + `discipline_engine`): a spread as one entry (list chip, combined numbers, every leg), the
 * plan-vs-actual lines (or "Went as planned."), the "From your broker" label and the "Sync from broker" button.
 * Made-up answers only; no DOM - parts rendered to markup.
 */

vi.mock("../utils/useMediaQuery", async (orig) => ({ ...(await orig<typeof import("../utils/useMediaQuery")>()), useMediaQuery: () => false }));

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const html = (node: ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
const NO_IMAGE = { image: vi.fn(() => new Promise<Blob>(() => undefined)) };
const BUY_SELL = /\b(buy|sell|bought|sold|target)\b/i;

afterEach(() => { vi.restoreAllMocks(); });

const SPREAD: NonNullable<JournalEntryFull["spread"]> = {
  group_id: "G1", structure: "Bull put spread", underlying: "DEMOIDX", legs: 2, closed: true, net_pnl: -3900, credit: 1500,
  credit_kind: "credit", planned_max_loss: 3500, actual_loss: 3900, charges: 20,
  leg_rows: [{ id: 1, symbol: "DEMOIDX 100 PE", direction: "SHORT", quantity: 50, entry_price: 50, exit_price: 110, pnl: -3000 },
    { id: 2, symbol: "DEMOIDX 90 PE", direction: "LONG", quantity: 50, entry_price: 20, exit_price: 2, pnl: -900 }],
};

const editor = (over: Partial<JournalEntryFull>) =>
  html(<EntryEditorV2 entry={{ ...DEMO_ENTRY, ...over }} api={journalApi} customTags={[]} onChanged={() => undefined} onRemoved={() => undefined} />);

describe("P2-8 J1 entry sheet", () => {
  it("shows a spread as one entry: combined P&L, credit, max loss planned vs taken and every leg", () => {
    const out = editor({ spread: SPREAD, plan_actual: ["Loss -₹3,900.00; the structure's planned max loss was -₹3,500.00."] });
    const t = text(out);
    expect(out).toContain('data-sheet-section="spread"');
    expect(t).toContain("DEMOIDX Bull put spread · 2 legs");
    expect(t).toContain("This leg: DEMOB");
    expect(t).toContain("Credit received");
    expect(t).toContain("Planned max loss");
    expect(t).toContain("Loss taken");
    expect(out.match(/<li/g)?.length).toBeGreaterThanOrEqual(2);
    expect(t).toContain("Short · DEMOIDX 100 PE");
    expect(t).toContain("planned max loss was");
    expect(text(out.split('data-sheet-section="spread"')[1].split("</section>")[0])).not.toMatch(BUY_SELL);
  });

  it("says 'Went as planned.' when plan and outcome agree, and lists only differences otherwise", () => {
    expect(editor({ plan_actual: [] })).toContain('data-plan-actual="same"');
    const diff = editor({ plan_actual: ["Stop moved from 98.00 to 97.00."] });
    expect(diff).toContain('data-plan-actual="diff"');
    expect(text(diff)).toContain("Stop moved from 98.00 to 97.00.");
    expect(editor({})).not.toContain("data-plan-actual");                // an older answer without the field: no section
  });

  it("titles the sheet with the structure's name for a spread leg", () => {
    expect(sheetTitle({ ...DEMO_ENTRY, spread: SPREAD })).toBe("DEMOIDX Bull put spread");
    expect(sheetTitle(DEMO_ENTRY)).toBe("DEMOB");
  });

  it("names a debit structure and an undefined max loss plainly", () => {
    const t = text(editor({ spread: { ...SPREAD, credit: -800, credit_kind: "debit", planned_max_loss: null, actual_loss: null } }));
    expect(t).toContain("Debit paid");
    expect(t).toContain("Not defined");
  });
});

describe("P2-8 J1 list", () => {
  const row = (over: Partial<JournalListRow>): JournalListRow => ({ ...DEMO_LIST.entries[0], ...over });
  const list = (entries: JournalListRow[], extra: Partial<Parameters<typeof JournalListV2>[0]> = {}) => html(
    <JournalListV2 state={{ status: "ok", data: { ...DEMO_LIST, entries } }} filters={{}} onFilters={() => undefined} onOpen={() => undefined}
      onAddManual={() => undefined} onRetry={() => undefined} onMore={() => undefined} api={NO_IMAGE}
      exportButtons={<ExportButtonsV2 onExport={() => undefined} busy={null} />} {...extra} />);

  it("shows a structure's legs as one row with a legs chip", () => {
    const out = list([row({ symbol: "DEMOIDX Bull put spread", legs: SPREAD.leg_rows, spread: SPREAD })]);
    expect(out).toContain("data-spread-row");
    expect(text(out)).toContain("2 legs");
  });

  it("labels an entry synced from his broker", () => {
    const t = text(list([row({ kind: "manual", outside_atp: true, origin: "BROKER", source_label: "From your broker - done outside ATP" })]));
    expect(t).toContain("From your broker");
  });

  it("offers Sync from broker only when the page passes it, and shows it busy while syncing", () => {
    expect(list([])).not.toContain("data-broker-sync");
    expect(text(list([], { onSyncBroker: () => undefined }))).toContain("Sync from broker");
    expect(text(list([], { onSyncBroker: () => undefined, syncing: true }))).toContain("Syncing…");
  });

  it("sends POST /journal/broker-sync", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ brokers: 1, read: 2, matched: 1, added: 1, closed: 0, already: 0, messages: [], entry_ids: [9] }),
      { status: 200, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
    const r = await journalApi.brokerSync();
    expect(r.added).toBe(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/journal/broker-sync");
    expect(init.method).toBe("POST");
    vi.unstubAllGlobals();
  });
});

describe("P2-8 J1 sync summary", () => {
  it("words what one sync did, in plain numbers", () => {
    expect(syncText(2, 1, 3)).toBe("2 trades added, 1 closed. 3 matched your ATP trades.");
    expect(syncText(1, 0, 0)).toBe("1 trade added.");
    expect(syncText(0, 0, 0)).toBe("No new trades from your broker.");
  });
});
