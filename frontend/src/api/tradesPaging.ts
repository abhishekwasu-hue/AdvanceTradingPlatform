/**
 * REV-1 M3: GET /api/trades is paged on the server (`limit`, `offset`; default page 200 rows, at most 500). A list asks
 * for one page at a time; a figure that needs the whole history (booked P&L, the equity curve) pages through to the end.
 */

/** Rows the server answers without a `limit` (backend app/trading/routes.py TRADES_PAGE_DEFAULT). */
export const TRADES_PAGE_DEFAULT = 200;
/** The largest page the server answers (TRADES_PAGE_MAX). */
export const TRADES_PAGE_MAX = 500;
/** A guard: a history longer than this many pages is cut here (500 x 40 = 20,000 trades). */
export const TRADES_MAX_PAGES = 40;

/** The query of one page, bounded to what the server accepts. */
export function tradesPageQuery(limit: number, offset = 0): string {
  const l = Math.min(TRADES_PAGE_MAX, Math.max(1, Math.floor(limit)));
  const o = Math.max(0, Math.floor(offset));
  return o > 0 ? `/trades?limit=${l}&offset=${o}` : `/trades?limit=${l}`;
}

/** Every page in order until a short page answers (no page is read twice). */
export async function readAllPages<T>(page: (limit: number, offset: number) => Promise<T[]>, size = TRADES_PAGE_MAX,
  maxPages = TRADES_MAX_PAGES): Promise<T[]> {
  const out: T[] = [];
  for (let i = 0; i < maxPages; i += 1) {
    const rows = await page(size, i * size);
    out.push(...rows);
    if (rows.length < size) break;
  }
  return out;
}

/** The newest `count` rows and whether older ones exist: reads `count + 1` (in pages of at most 500). */
export async function readNewest<T>(page: (limit: number, offset: number) => Promise<T[]>, count: number): Promise<{ rows: T[]; more: boolean }> {
  const want = Math.max(1, Math.floor(count)) + 1;
  const out: T[] = [];
  while (out.length < want) {
    const size = Math.min(TRADES_PAGE_MAX, want - out.length);
    const rows = await page(size, out.length);
    out.push(...rows);
    if (rows.length < size) break;
  }
  return { rows: out.slice(0, want - 1), more: out.length >= want };
}
