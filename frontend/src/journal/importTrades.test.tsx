import type { ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import { journalApi } from "./api";
import { EntryEditorV2 } from "./EntrySheetV2";
import { ACCEPT, ImportPreviewV2, ImportTradesFormV2, importWhy, previewFile } from "./ImportTradesV2";
import { JournalListV2 } from "./JournalListV2";
import { DEMO_ENTRY, DEMO_LIST } from "./journal.fixture";
import { TradeChartV2, chartMarks } from "./TradeChartV2";
import type { ImportPreview, TradeChart } from "./types";

/**
 * M88: the Journal's "Import from a file" (the preview, the column choices, the counts, the problems, the trades) and
 * "Show on chart" for an outside-ATP trade, on made-up answers. No DOM: parts are rendered to markup and their pure
 * helpers called directly.
 */

vi.mock("../utils/useMediaQuery", async (orig) => ({ ...(await orig<typeof import("../utils/useMediaQuery")>()), useMediaQuery: () => false }));

const text = (html: string) => html.replace(/<[^>]+>/g, " ").replace(/&#x27;/g, "'").replace(/&quot;/g, "\"").replace(/&amp;/g, "&").replace(/\s+/g, " ");
const html = (node: ReactNode) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>);
const BUY_SELL = /\b(buy|sell|buying|selling)\b/i;

const PREVIEW: ImportPreview = {
  file: { name: "tradebook.csv", size: 900, hash: "h" },
  layout: { id: "zerodha", label: "Zerodha Console tradebook" },
  shape: "fills",
  pairing: "The fills of one symbol are paired first-in, first-out.",
  headers: ["symbol", "trade_type", "quantity", "price", "order_execution_time"],
  mapping: { symbol: "symbol", side: "trade_type", quantity: "quantity", price: "price", time: "order_execution_time", charges: null },
  fields: [{ key: "symbol", label: "Symbol" }, { key: "side", label: "Side" }, { key: "charges", label: "Charges" }],
  counts: { rows: 5, problems: 1, trades: 2, duplicates: 1, to_import: 1, open: 0, date_only: 0 },
  problems: [{ row: 4, reason: "No quantity." }],
  trades: [
    { symbol: "AAAA", exchange: "NSE", instrument_kind: "UNDERLYING", direction: "LONG", quantity: 20, entry_time: "2026-10-05T03:50:00+00:00",
      entry_price: 105, exit_time: "2026-10-06T05:30:00+00:00", exit_price: 120, charges: 0, pnl: 300, stop_loss: null, rows: [2, 3, 5],
      date_only: false, duplicate: false },
    { symbol: "BBBB", exchange: "NSE", instrument_kind: "UNDERLYING", direction: "SHORT", quantity: 5, entry_time: "2026-10-07T04:00:00+00:00",
      entry_price: 200, exit_time: null, exit_price: null, charges: 0, pnl: null, stop_loss: null, rows: [6], date_only: false, duplicate: true },
  ],
  already_imported: null,
  limits: { max_bytes: 5242880, max_rows: 5000, max_trades: 2000 },
};

describe("M88 import from a file", () => {
  it("the Trades list has the import button beside the outside-ATP one, only when wired", () => {
    const base = { state: { status: "ok" as const, data: DEMO_LIST }, filters: {}, onFilters: () => undefined, onOpen: () => undefined,
      onAddManual: () => undefined, onRetry: () => undefined, onMore: () => undefined, api: { image: vi.fn() }, exportButtons: null };
    const on = html(<JournalListV2 {...base} onImport={() => undefined} />);
    expect(on).toContain("data-import-trades");
    expect(text(on)).toMatch(/Add a trade done outside ATP\s+Import from a file/);
    expect(html(<JournalListV2 {...base} />)).not.toContain("data-import-trades");
  });

  it("the empty form asks for a CSV or Excel file and cannot import yet", () => {
    const api = { importPreview: vi.fn(), importFile: vi.fn() };
    const out = html(<ImportTradesFormV2 api={api} onImported={() => undefined} onCancel={() => undefined} />);
    expect(out).toContain(`accept="${ACCEPT}"`);
    expect(ACCEPT).toBe(".csv,.xlsx,.xls");
    expect(text(out)).toContain("Zerodha, Upstox, Angel One, Dhan, Groww or Fyers");
    // owner 18 Nov: no greyed-out Import before a file; the main button opens the file picker and says why
    expect(out).not.toContain("data-import-save");
    expect(out).toContain("data-import-pick");
    expect(text(out)).toContain("Choose your broker's tradebook file first.");
    expect(api.importPreview).not.toHaveBeenCalled();
  });

  it("says why there is nothing to import (owner 18 Nov)", () => {
    const file = new File(["x"], "t.csv");
    const preview = (counts: Partial<ImportPreview["counts"]>, already: ImportPreview["already_imported"] = null) => ({
      status: "ok" as const, file, preview: { already_imported: already, counts: { rows: 3, trades: 2, duplicates: 0, problems: 0, to_import: 2, date_only: 0, ...counts } } as unknown as ImportPreview });
    expect(importWhy({ status: "idle" })).toMatch(/Choose/);
    expect(importWhy({ status: "reading", file })).toBeNull();
    expect(importWhy({ status: "error", file, error: new Error("x") })).toMatch(/could not be read/);
    expect(importWhy(preview({}))).toBeNull();
    expect(importWhy(preview({ to_import: 0 }, { at: "2026-11-01T00:00:00Z" } as ImportPreview["already_imported"]))).toBe("This file is already in your journal.");
    expect(importWhy(preview({ to_import: 0, duplicates: 2 }))).toMatch(/Every trade/);
    expect(importWhy(preview({ to_import: 0, trades: 0, problems: 3 }))).toMatch(/No complete trades/);
  });

  it("the preview shows the layout, the counts, the pairing method, every column choice, the problems and the trades", () => {
    const onColumn = vi.fn();
    const out = html(<ImportPreviewV2 p={PREVIEW} onColumn={onColumn} />);
    const t = text(out);
    expect(t).toContain("Read as Zerodha Console tradebook");
    expect(t).toMatch(/Rows read 5 Trades found 2 Already in journal 1 Rows with a problem 1/);
    expect(t).toContain("How the fills become trades");
    expect(t).toContain("first-in, first-out");
    expect(t).toContain("Row 4 No quantity.");
    expect(t).toContain("AAAA Long 20");
    expect(t).toContain("Entry 5 Oct 2026, 09:20 IST at 105.00 · Exit 6 Oct 2026, 11:00 IST at 120.00");
    expect(t).toContain("Already in your journal");
    expect(t).toContain("still open");
    expect(out).toContain('data-import-map="charges"');
    expect(out).toContain("Not in this file");
    expect(t).not.toMatch(BUY_SELL);                     // structure words only: long / short, entry / exit
  });

  it("a file imported before says so", () => {
    const t = text(html(<ImportPreviewV2 p={{ ...PREVIEW, already_imported: { at: "2026-11-01T05:00:00+00:00", file_name: "tradebook.csv", trades_left: 2 } }} onColumn={() => undefined} />));
    expect(t).toContain("This file was imported on 1 Nov 2026");
  });

  it("previewFile sends the file and his column choices; the API posts multipart to the import routes", async () => {
    const importPreview = vi.fn(async () => PREVIEW);
    const file = new File(["a,b"], "mine.csv", { type: "text/csv" });
    expect(await previewFile({ importPreview, importFile: vi.fn() }, file, { symbol: "a" })).toBe(PREVIEW);
    expect(importPreview).toHaveBeenCalledWith(file, "mine.csv", { symbol: "a" });
    const fetchMock = vi.fn<(url: string, init?: RequestInit) => Promise<Response>>(async () => new Response(JSON.stringify(PREVIEW), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    try {
      await journalApi.importPreview(file, "mine.csv", { symbol: "a" });
      await journalApi.importFile(file, "mine.csv");
      const [url1, init1] = fetchMock.mock.calls[0];
      const [url2, init2] = fetchMock.mock.calls[1];
      expect(url1).toMatch(/\/journal\/import\/preview$/);
      expect(url2).toMatch(/\/journal\/import$/);
      expect(init1?.method).toBe("POST");
      const form = init1?.body as FormData;
      expect(form.get("mapping")).toBe(JSON.stringify({ symbol: "a" }));
      expect((init2?.body as FormData).get("mapping")).toBeNull();
    } finally { vi.unstubAllGlobals(); }
  });
});

const CHART: TradeChart = {
  symbol: "AAAA", exchange: "NSE", direction: "LONG", timeframe: "day", timeframe_label: "Daily candles", note: "Daily candles from the end-of-day exchange files.",
  candles: [{ timestamp: "2026-10-04T18:30:00+00:00", open: 1, high: 2, low: 1, close: 2, volume: 1 }],
  marks: [
    { kind: "entry", label: "Entry", at: "2026-10-05T03:50:00+00:00", price: 105, side: "BUY", bar: "2026-10-04T18:30:00+00:00" },
    { kind: "exit", label: "Exit", at: "2026-10-06T05:30:00+00:00", price: 120, side: "SELL", bar: null },
  ],
  levels: [{ kind: "stop", label: "Your stop", price: 98 }],
  missing: false, missing_text: null, image: "/api/journal/manual/1/trade-chart.png",
};

describe("M88 show on chart", () => {
  it("the marks sit on their candle at his own prices; a mark with no candle is not drawn; his stop is a line", () => {
    const { markers, lines } = chartMarks(CHART);
    expect(markers).toEqual([{ timestamp: "2026-10-04T18:30:00+00:00", position: "belowBar", color: "up", shape: "arrowUp", text: "Entry 105.00" }]);
    expect(lines).toEqual([{ price: 105, color: "up", title: "Entry" }, { price: 120, color: "down", title: "Exit" }, { price: 98, color: "fg", title: "Your stop" }]);
    const short = chartMarks({ ...CHART, marks: [{ ...CHART.marks[0], side: "SELL" }] }).markers[0];
    expect([short.position, short.color, short.shape]).toEqual(["aboveBar", "down", "arrowDown"]);
  });

  it("an option on its underlying's chart: the mark names the premium and draws no price line (owner 19 Nov)", () => {
    const onIndex = { ...CHART, marks: CHART.marks.map((m) => ({ ...m, on_chart: false })), levels: [] };
    const { markers, lines } = chartMarks(onIndex);
    expect(markers[0].text).toBe("Entry (premium 105.00)");
    expect(lines).toEqual([]);
  });

  it("an outside-ATP entry has 'Show on chart' in its Charts section; an ATP trade does not", () => {
    const charts = { ...DEMO_ENTRY.charts, trade: "/api/journal/manual/9/trade-chart", trade_image: "/api/journal/manual/9/trade-chart.png" };
    const manual = html(<EntryEditorV2 entry={{ ...DEMO_ENTRY, kind: "manual", manual: true, imported: true, id: 9, charts }} api={journalApi}
      customTags={[]} onChanged={() => undefined} onRemoved={() => undefined} />);
    expect(manual).toContain("data-show-on-chart");
    const atp = html(<EntryEditorV2 entry={DEMO_ENTRY} api={journalApi} customTags={[]} onChanged={() => undefined} onRemoved={() => undefined} />);
    expect(atp).not.toContain("data-show-on-chart");
  });

  it("closed, it is one 'Show on chart' button and loads nothing", () => {
    const api = { tradeChart: vi.fn(), image: vi.fn() };
    const out = html(<TradeChartV2 api={api} path="/api/journal/manual/1/trade-chart" imagePath={CHART.image} fileName="a.png" />);
    expect(text(out).trim()).toBe("Show on chart");
    expect(out).toContain("data-show-on-chart");
    expect(out).not.toMatch(/href=/);                      // in place: no navigation away
    expect(api.tradeChart).not.toHaveBeenCalled();
  });
});

describe("the journal chart's timeframe (owner 19 Nov)", () => {
  it("15 minutes first, his choice of 5m / 1h / Daily, the path carries it", async () => {
    const { DEFAULT_TRADE_TF, TRADE_CHART_TFS, withTf } = await import("./TradeChartV2");
    expect(DEFAULT_TRADE_TF).toBe("15m");
    expect(TRADE_CHART_TFS.map((t) => t.key)).toEqual(["5m", "15m", "1h", "1d"]);
    expect(withTf("/api/journal/manual/9/trade-chart", "1h")).toBe("/api/journal/manual/9/trade-chart?tf=1h");
    expect(withTf("/api/journal/manual/9/trade-chart.png?tf=1d&cvd=true", "15m")).toBe("/api/journal/manual/9/trade-chart.png?tf=15m&cvd=true");
  });
});
