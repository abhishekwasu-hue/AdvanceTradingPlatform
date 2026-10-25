import { useId, useState, type ReactNode } from "react";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";

/**
 * UI-H Wave 0 (H5): one table that works on a phone.
 * - `phone="scroll"` (default): the first column is sticky while the rest scrolls sideways inside the table's box (the
 *   page itself never scrolls sideways);
 * - `phone="stack"`: below md each row becomes a small card of "header: value" lines;
 * - `collapsible`: when the page already shows the same numbers as a chart, the table sits behind a "Show table" button
 *   (closed at first).
 * Numbers are right-aligned and tabular (`align: "right"`).
 */
export interface Column<R> {
  key: string;
  header: string;
  align?: "left" | "right";
  cell: (row: R) => ReactNode;
}

export interface ResponsiveTableProps<R> {
  caption: string;
  columns: readonly Column<R>[];
  rows: readonly R[];
  rowKey: (row: R, index: number) => string;
  phone?: "scroll" | "stack";
  collapsible?: boolean;
  /** The words when there are no rows. */
  empty?: ReactNode;
  className?: string;
}

export function ResponsiveTable<R>({ caption, columns, rows, rowKey, phone = "scroll", collapsible = false, empty = "Nothing to show yet.", className = "" }: ResponsiveTableProps<R>) {
  const isPhone = useMediaQuery(PHONE_QUERY);
  const [open, setOpen] = useState(!collapsible);
  const id = useId();
  const stacked = isPhone && phone === "stack";

  const toggle = collapsible && (
    <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)} data-table-toggle
      className="inline-flex min-h-11 items-center rounded-control border border-border bg-surface-2 px-space-3 text-t14 font-semibold text-fg hover:bg-surface-3 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-9">
      {open ? "Hide table" : "Show table"}
    </button>
  );

  let body: ReactNode = null;
  if (open) {
    if (rows.length === 0) body = <p className="text-t14 text-fg-muted">{empty}</p>;
    else if (stacked) {
      body = (
        <ul aria-label={caption} className="space-y-space-2" data-table-stacked>
          {rows.map((r, i) => (
            <li key={rowKey(r, i)} className="rounded-control border border-subtle bg-surface-1 p-space-3">
              <p className="text-t14 font-semibold text-fg">{columns[0]?.cell(r)}</p>
              <dl className="mt-space-1 grid grid-cols-2 gap-x-space-3 gap-y-space-1 text-t14">
                {columns.slice(1).map((c) => (
                  <div key={c.key} className="contents">
                    <dt className="text-fg-muted">{c.header}</dt>
                    <dd className={`m-0 text-fg ${c.align === "right" ? "text-right font-tabular" : ""}`}>{c.cell(r)}</dd>
                  </div>
                ))}
              </dl>
            </li>
          ))}
        </ul>
      );
    } else {
      body = (
        <div className="max-w-full overflow-x-auto rounded-control border border-subtle" data-table-scroll>
          <table className="w-full border-collapse text-t14">
            <caption className="sr-only">{caption}</caption>
            <thead>
              <tr className="bg-surface-2">
                {columns.map((c, ci) => (
                  <th key={c.key} scope="col"
                    className={`whitespace-nowrap px-space-3 py-space-2 font-semibold text-fg-muted ${c.align === "right" ? "text-right" : "text-left"} ${ci === 0 ? "sticky left-0 z-10 bg-surface-2" : ""}`}>
                    {c.header}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={rowKey(r, i)} className="border-t border-subtle">
                  {columns.map((c, ci) => ci === 0 ? (
                    <th key={c.key} scope="row" className="sticky left-0 z-10 whitespace-nowrap bg-surface-1 px-space-3 py-space-2 text-left font-medium text-fg">{c.cell(r)}</th>
                  ) : (
                    <td key={c.key} className={`whitespace-nowrap px-space-3 py-space-2 text-fg ${c.align === "right" ? "text-right font-tabular" : ""}`}>{c.cell(r)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    }
  }

  return (
    <div className={`min-w-0 space-y-space-2 ${className}`} data-responsive-table>
      {toggle}
      <div id={id} hidden={!open}>{body}</div>
    </div>
  );
}
