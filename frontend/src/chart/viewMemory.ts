/**
 * P1-4 (owner order "ATP session: chart and colour", Part 2.5; chart review R9): the zoom and the price-scale mode of a
 * chart are remembered per user, symbol, exchange and timeframe on this device and restored when the chart opens;
 * "Reset" forgets them.
 *
 * R9: a visible *logical* range means something only for one bar array (older bars, a new session or another history
 * window shift it), so the range is not stored. Stored instead: the bar spacing (the zoom), the scroll position from
 * the right edge (bars from the latest; the library's `scrollPosition`), and whether the price scale is on Auto. The
 * price range is never stored (prices move). Writes are debounced by the chart (about 500 ms after the last change);
 * the list is capped at MAX_VIEWS (the oldest go); every storage access is in try / catch and an empty or broken store
 * reads as "nothing remembered".
 */
export const VIEW_MEMORY_KEY = "atp_chart_view:1";
export const MAX_VIEWS = 50;
export const SAVE_DELAY_MS = 500;

export interface RememberedView { barSpacing: number; position: number; autoScale: boolean; at: number }
type Store = Record<string, Record<string, RememberedView>>;     // user -> chart key -> view

function read(): Store {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(VIEW_MEMORY_KEY) ?? "null");
    return raw && typeof raw === "object" && !Array.isArray(raw) ? raw as Store : {};
  } catch {
    return {};
  }
}

function write(store: Store): void {
  try { localStorage.setItem(VIEW_MEMORY_KEY, JSON.stringify(store)); } catch { /* intentional: storage unavailable - lasts this visit only */ }
}

function valid(v: unknown): v is RememberedView {
  const o = v as Partial<RememberedView> | null;
  return !!o && typeof o === "object" && Number.isFinite(o.barSpacing) && (o.barSpacing ?? 0) > 0 && Number.isFinite(o.position)
    && typeof o.autoScale === "boolean";
}

/** One user's remembered views on this device. `chartKey`: symbol | exchange | timeframe (the chart's data key). */
export class ViewMemory {
  constructor(private readonly user: string) {}

  load(chartKey: string): RememberedView | null {
    const v = read()[this.user]?.[chartKey];
    return valid(v) ? v : null;
  }

  save(chartKey: string, view: Omit<RememberedView, "at">, now = Date.now()): void {
    if (!chartKey || !valid({ ...view, at: now })) return;
    const store = read();
    const mine = { ...(store[this.user] ?? {}), [chartKey]: { ...view, at: now } };
    const keys = Object.keys(mine).sort((a, b) => (mine[b].at ?? 0) - (mine[a].at ?? 0));
    for (const old of keys.slice(MAX_VIEWS)) delete mine[old];
    write({ ...store, [this.user]: mine });
  }

  forget(chartKey: string): void {
    const store = read();
    if (!store[this.user]?.[chartKey]) return;
    const mine = { ...store[this.user] };
    delete mine[chartKey];
    write({ ...store, [this.user]: mine });
  }
}
