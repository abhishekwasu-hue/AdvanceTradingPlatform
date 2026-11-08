import { FileUp } from "lucide-react";
import { useRef, useState } from "react";
import { errorSentence } from "../api/errors";
import { Sheet } from "../components/primitives/Dialog";
import { useToast } from "../components/Toast";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, LABEL } from "../trading/PartsV2";
import { chipClass } from "../ui/Chip";
import { Combobox } from "../ui/Combobox";
import { Details } from "../ui/Details";
import { formatDateIST, formatDateTimeIST, formatNumber, formatPrice } from "../ui/format";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";
import type { JournalApi } from "./api";
import { PnlV2 } from "./JournalPartsV2";
import type { ImportMapping, ImportPreview, ImportResult, ImportTrade } from "./types";

/**
 * M88: "Import from a file" - trades done outside ATP from a CSV or Excel file (a broker tradebook or his own sheet).
 * A preview first: the rows read, the rows with a problem (and why), the trades and the ones already in his journal,
 * with every column choice his to change. Nothing is saved until he presses Import. Journal data only: no order, no
 * position, no broker call.
 */

export type ImportApi = Pick<JournalApi, "importPreview" | "importFile">;
export const ACCEPT = ".csv,.xlsx,.xls";
const NOT_IN_FILE = "";

type State =
  | { status: "idle" }
  | { status: "reading"; file: File }
  | { status: "error"; file: File | null; error: unknown }
  | { status: "ok"; file: File; preview: ImportPreview };

/** Reads the file again under the chosen columns (the server is the one parser; nothing is stored). */
export async function previewFile(api: ImportApi, file: File, mapping?: ImportMapping): Promise<ImportPreview> {
  return api.importPreview(file, file.name, mapping);
}

const when = (iso: string, dateOnly: boolean) => (dateOnly ? formatDateIST(iso, { year: true }) : formatDateTimeIST(iso, { year: true }));

function TradeRow({ t }: { t: ImportTrade }) {
  return (
    <li className="flex items-start justify-between gap-space-3 py-space-2" data-import-trade={t.symbol}>
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-space-2 text-t14 font-semibold text-fg">
          <span className="truncate">{t.symbol}</span>
          <span className="font-normal text-fg-muted">{t.direction === "LONG" ? "Long" : "Short"} {formatNumber(t.quantity, 2, { trim: true })}</span>
          {t.duplicate && <span className={chipClass({ tone: "neutral" })}>Already in your journal</span>}
        </p>
        <p className="text-t13 text-fg-muted">
          <span className="font-tabular">Entry {when(t.entry_time, t.date_only)} at {formatPrice(t.entry_price)}</span>
          {t.exit_time && t.exit_price != null
            ? <span className="font-tabular"> · Exit {when(t.exit_time, t.date_only)} at {formatPrice(t.exit_price)}</span>
            : <span> · still open</span>}
        </p>
      </div>
      <span className="shrink-0 text-right"><PnlV2 value={t.pnl} /></span>
    </li>
  );
}

function Count({ label, value, tone }: { label: string; value: number; tone?: "warn" | "up" }) {
  const colour = tone === "warn" && value > 0 ? "text-warn" : tone === "up" ? "text-up" : "text-fg";
  return (
    <div className="rounded-control border border-subtle bg-surface-2 px-space-3 py-space-2">
      <p className="text-t12 text-fg-muted">{label}</p>
      <p className={`font-tabular text-t16 font-semibold ${colour}`}>{formatNumber(value)}</p>
    </div>
  );
}

/** The preview of a file (stateless): what it was read as, the counts, the columns, the problems and the trades. */
export function ImportPreviewV2({ p, onColumn }: { p: ImportPreview; onColumn: (key: string, header: string) => void }) {
  return (
    <>
      <div className="flex flex-wrap items-center gap-space-2 text-t13">
        <span className="text-fg-muted">Read as</span><span className={chipClass({ tone: "info" })} data-import-layout={p.layout.id}>{p.layout.label}</span>
      </div>
      {p.already_imported && (
        <p role="alert" className="rounded-panel border border-warn/40 bg-warn-tint px-space-4 py-space-3 text-t14 text-fg" data-import-already>
          This file was imported on {formatDateIST(p.already_imported.at, { year: true })}; its trades are already in your journal.
        </p>
      )}
      <div className="grid grid-cols-2 gap-space-2" data-import-counts>
        <Count label="Rows read" value={p.counts.rows} />
        <Count label="Trades found" value={p.counts.trades} />
        <Count label="Already in journal" value={p.counts.duplicates} />
        <Count label="Rows with a problem" value={p.counts.problems} tone="warn" />
      </div>
      {p.pairing && (
        <details className="text-t13 text-fg-muted" data-import-pairing>
          <summary className="cursor-pointer text-fg">How the fills become trades</summary>
          <p className="mt-space-1">{p.pairing}</p>
        </details>
      )}

      <details className="rounded-panel border border-subtle px-space-3 py-space-2" data-import-columns>
        <summary className="cursor-pointer text-t14 font-semibold text-fg">Columns</summary>
        <p className="mt-space-1 text-t13 text-fg-muted">Found by their names. Change any of them; the preview reads the file again.</p>
        <div className="mt-space-3 grid grid-cols-1 gap-space-3 sm:grid-cols-2">
          {p.fields.map((f) => (
            <label key={f.key} className="min-w-0">
              <span className={LABEL}>{f.label}</span>
              <Combobox label={f.label} value={p.mapping[f.key] ?? NOT_IN_FILE} onChange={(v) => onColumn(f.key, v)} dataAttrs={{ "data-import-map": f.key }}
                options={[{ value: NOT_IN_FILE, label: "Not in this file" }, ...p.headers.map((h) => ({ value: h, label: h }))]} searchable={p.headers.length > 12} />
            </label>
          ))}
        </div>
      </details>

      {p.problems.length > 0 && (
        <details className="rounded-panel border border-warn/40 px-space-3 py-space-2" data-import-problems open={p.counts.trades === 0}>
          <summary className="cursor-pointer text-t14 font-semibold text-fg">Rows with a problem ({formatNumber(p.counts.problems)}) - left out</summary>
          <ul className="mt-space-2 space-y-space-1 text-t13">
            {p.problems.map((x) => (
              <li key={`${x.row}-${x.reason}`} className="flex gap-space-2" data-import-problem={x.row}>
                <span className="w-16 shrink-0 font-tabular text-fg-muted">Row {x.row}</span><span className="min-w-0 text-fg">{x.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {p.trades.length > 0 && (
        <section aria-label="Trades in the file">
          <h3 className="text-t14 font-semibold text-fg">Trades in the file</h3>
          {p.counts.date_only > 0 && <p className="text-t13 text-fg-muted">Rows with a date and no time show the date only.</p>}
          <ul className="divide-y divide-subtle" data-import-trades>{p.trades.map((t, i) => <TradeRow key={`${t.symbol}-${t.entry_time}-${i}`} t={t} />)}</ul>
          {p.trades.length < p.counts.trades && <p className="text-t13 text-fg-muted">The first {formatNumber(p.trades.length)} of {formatNumber(p.counts.trades)} are listed.</p>}
        </section>
      )}
    </>
  );
}

export function ImportTradesFormV2({ api, onImported, onCancel }: { api: ImportApi; onImported: (r: ImportResult) => void; onCancel: () => void }) {
  const toast = useToast();
  const fileRef = useRef<HTMLInputElement>(null);
  const [state, setState] = useState<State>({ status: "idle" });
  const [busy, setBusy] = useState(false);

  async function read(file: File, mapping?: ImportMapping) {
    setState({ status: "reading", file });
    try { setState({ status: "ok", file, preview: await previewFile(api, file, mapping) }); }
    catch (error) { setState({ status: "error", file, error }); }
  }

  function changeColumn(key: string, header: string) {
    if (state.status !== "ok") return;
    void read(state.file, { ...state.preview.mapping, [key]: header === NOT_IN_FILE ? null : header });
  }

  async function save() {
    if (state.status !== "ok") return;
    setBusy(true);
    try {
      const r = await api.importFile(state.file, state.file.name, state.preview.mapping);
      if (r.already_imported) toast.info("This file is already in your journal.");
      else toast.success(r.imported === 1 ? "Imported 1 trade." : `Imported ${formatNumber(r.imported)} trades.`);
      onImported(r);
    } catch (e) { toast.error(errorSentence(e)); } finally { setBusy(false); }
  }

  const p = state.status === "ok" ? state.preview : null;
  return (
    <div className="space-y-space-4" data-import-form>
      <input ref={fileRef} type="file" accept={ACCEPT} className="sr-only" aria-label="Choose a file to import" data-import-file
        onChange={(e) => { const f = e.target.files?.[0]; if (f) void read(f); e.target.value = ""; }} />
      <div className="space-y-space-2">
        <button type="button" className={BUTTON_SECONDARY} onClick={() => fileRef.current?.click()} disabled={state.status === "reading"} data-import-choose>
          <FileUp size={16} aria-hidden />{state.status === "idle" ? "Choose a file" : "Choose another file"}
        </button>
        <p className="text-t13 text-fg-muted">CSV or Excel, up to 5 MB.</p>
        <Details id="journal.import.formats" label="Which files">
          <p>A tradebook from Zerodha, Upstox, Angel One, Dhan, Groww or Fyers (.csv, .xlsx or .xls), or your own sheet with symbol, side, quantity, entry and exit.</p>
        </Details>
        {state.status !== "idle" && state.file && <p className="truncate text-t13 text-fg" data-import-name>{state.file.name}</p>}
      </div>

      {state.status === "reading" && <p className="text-t14 text-fg-muted" role="status">Reading the file…</p>}
      {state.status === "error" && (
        <p role="alert" className="rounded-panel border border-down/40 bg-down-tint px-space-4 py-space-3 text-t14 text-fg" data-import-error>{errorSentence(state.error)}</p>
      )}

      {p && <ImportPreviewV2 p={p} onColumn={changeColumn} />}

      <div className="flex flex-wrap justify-end gap-space-2 border-t border-subtle pt-space-3">
        <button type="button" className={BUTTON_SECONDARY} onClick={onCancel}>Cancel</button>
        <button type="button" className={BUTTON_PRIMARY} disabled={!p || p.counts.to_import === 0 || busy} onClick={() => void save()} data-import-save>
          {busy ? "Importing…" : p && p.counts.to_import > 0 ? `Import ${formatNumber(p.counts.to_import)} ${p.counts.to_import === 1 ? "trade" : "trades"}` : "Import"}
        </button>
      </div>
    </div>
  );
}

export function ImportTradesSheetV2({ open, api, onClose, onImported }: {
  open: boolean; api: ImportApi; onClose: () => void; onImported: (r: ImportResult) => void;
}) {
  const phone = useMediaQuery(PHONE_QUERY);
  return (
    <Sheet open={open} onOpenChange={(o) => { if (!o) onClose(); }} side={phone ? "bottom" : "right"} title="Import trades from a file">
      {open && <ImportTradesFormV2 api={api} onCancel={onClose} onImported={(r) => { onImported(r); onClose(); }} />}
    </Sheet>
  );
}
