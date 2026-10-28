import type { ReactNode } from "react";
import { cx } from "./cx";

export interface Column<T> {
  key: string;
  header: ReactNode;
  cell: (row: T) => ReactNode;
  /** Numbers right-aligned with tabular digits. */
  numeric?: boolean;
  className?: string;
}

const NUMBER_LIKE = /^[+−-]?\s?₹?\s?[\d,]+(\.\d+)?\s?%?$|^[–-]$/;

/** UI-28 U3.9 (NN/g H4): a column whose cells are all numbers (or the dash for none) is numeric unless it says otherwise. */
export function isNumericColumn<T>(column: Pick<Column<T>, "cell" | "numeric">, rows: readonly T[]): boolean {
  if (column.numeric !== undefined) return column.numeric;
  const shown = rows.slice(0, 20).map((r) => column.cell(r));
  return shown.length > 0 && shown.every((v) => typeof v === "number" || (typeof v === "string" && NUMBER_LIKE.test(v.trim())))
    && shown.some((v) => typeof v === "number" || (typeof v === "string" && /\d/.test(v)));
}

/** A dense data table: sticky header, tabular numbers, right-aligned numeric columns, horizontal scroll on phones. */
export function Table<T>({ columns: given, rows, rowKey, empty, caption }: {
  columns: Column<T>[]; rows: T[]; rowKey: (row: T) => string | number; empty?: ReactNode; caption?: string;
}) {
  if (rows.length === 0 && empty) return <>{empty}</>;
  const columns = given.map((c) => ({ ...c, numeric: isNumericColumn(c, rows) }));
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full border-collapse text-sm">
        {caption && <caption className="sr-only">{caption}</caption>}
        <thead className="sticky top-0 bg-surface-2">
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={cx("whitespace-nowrap px-3 py-2 text-left text-xs font-semibold uppercase tracking-wide text-fg-muted", c.numeric && "text-right", c.className)}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={rowKey(r)} className="border-t border-border hover:bg-surface-2/60">
              {columns.map((c) => (
                <td key={c.key} className={cx("px-3 py-2 text-fg", c.numeric && "text-right font-tabular", c.className)}>{c.cell(r)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
