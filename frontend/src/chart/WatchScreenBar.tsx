import { Bell, Filter } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { cx } from "../components/primitives/cx";
import {
  NO_CHOICE, ScreenChecker, checkList, countText, watchScreenApi,
  type CheckOutcome, type ListChoice, type OwnScreen, type WatchScreenApi,
} from "./watchScreen";
import type { WatchItem } from "./watchlistsV2";

/**
 * M91 W1-W4: the list's "Screen" row at the top of a watchlist - the dropdown (None / his own saved screens), "Matches
 * only", the "12 of 80 match" count and the alert choice (OFF until he turns it on). The state lives in `useWatchScreen`;
 * the panel orders and dims its rows from `matched`.
 */
const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";

const tabHidden = () => typeof document !== "undefined" && document.visibilityState === "hidden";

/** His own screens and every list's choice, read once per panel - only while the flag `watchlist_screen` is on (`enabled`). Off,
 * a 503 or any failure: `off` - no row, the panel exactly as before. */
export function useScreenChoices(enabled: boolean, api: Pick<WatchScreenApi, "screens" | "settings" | "save"> = watchScreenApi) {
  const [screens, setScreens] = useState<OwnScreen[] | null>(null);
  const [lists, setLists] = useState<Record<string, ListChoice>>({});
  const [off, setOff] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!enabled) { setOff(true); return undefined; }
    setOff(false);
    let live = true;
    Promise.all([api.screens(), api.settings()]).then(([s, c]) => {
      if (!live) return;
      setScreens(Array.isArray(s?.screens) ? s.screens : []);
      setLists(c?.lists && typeof c.lists === "object" ? c.lists : {});
    }, () => { if (live) setOff(true); });
    return () => { live = false; };
  }, [api, enabled]);
  const choose = useCallback(async (key: string, choice: ListChoice) => {
    const before = lists[key];
    setLists((m) => { const next = { ...m }; if (choice.screen_id == null) delete next[key]; else next[key] = choice; return next; });
    setError(null);
    try {
      await api.save(key, choice);
    } catch {
      setLists((m) => { const next = { ...m }; if (before) next[key] = before; else delete next[key]; return next; });
      setError("Your choice could not be saved just now. Try again.");
    }
  }, [api, lists]);
  return { screens, lists, off, error, choose };
}

export interface WatchScreenState {
  choice: ListChoice;
  screen: OwnScreen | null;
  /** Item keys (EXCHANGE:SYMBOL) that meet the screen at the last check. */
  matched: ReadonlySet<string>;
  outcome: CheckOutcome | null;
  checking: boolean;
  failed: boolean;
}

/**
 * W2 / W3: the list on screen checked against its chosen screen (`ScreenChecker`: at once, when its rows or its screen
 * change, and again when the next bar of the screen's timeframe has closed). Nothing runs while `active` is false (the
 * list is not on screen); a check due while the tab is hidden waits for it to show.
 */
export function useWatchScreen(args: { listKey: string; listName: string; items: readonly WatchItem[]; choice: ListChoice; screen: OwnScreen | null; active: boolean },
  api: Pick<WatchScreenApi, "check"> = watchScreenApi): WatchScreenState {
  const { listKey, listName, items, choice, screen, active } = args;
  const [outcome, setOutcome] = useState<CheckOutcome | null>(null);
  const [checking, setChecking] = useState(false);
  const [failed, setFailed] = useState(false);
  const checker = useMemo(() => new ScreenChecker({
    check: (t, stopped, onProgress) => checkList(api, t, { stopped, onProgress: (o) => onProgress({ ...o, matched: new Set(o.matched) }) })
      .then((o) => ({ ...o, matched: new Set(o.matched) })),
    onOutcome: setOutcome, onBusy: setChecking, onFailed: setFailed, hidden: tabHidden, now: () => Date.now(),
    setTimer: (fn, ms) => window.setTimeout(fn, ms), clearTimer: (t) => window.clearTimeout(t as number),
  }), [api]);
  useEffect(() => () => checker.close(), [checker]);
  useEffect(() => {
    const shown = () => checker.visible();
    document.addEventListener("visibilitychange", shown);
    return () => document.removeEventListener("visibilitychange", shown);
  }, [checker]);
  const screenId = screen?.usable ? choice.screen_id : null;
  useEffect(() => {
    checker.update(active && screenId != null && items.length > 0, screenId != null ? { listKey, listName, screenId, items } : null);
  }, [checker, active, screenId, listKey, listName, items]);
  return { choice, screen, matched: (screenId != null ? outcome?.matched : undefined) ?? EMPTY_SET, outcome, checking, failed };
}

const EMPTY_SET: ReadonlySet<string> = new Set();

export interface WatchScreenBarProps {
  listKey: string;
  screens: readonly OwnScreen[];
  choice: ListChoice;
  onChoose: (choice: ListChoice) => void;
  state: WatchScreenState;
  items: readonly WatchItem[];
  touch?: boolean;
  error?: string | null;
}

export function WatchScreenBar({ listKey, screens, choice, onChoose, state, items, touch = false, error = null }: WatchScreenBarProps) {
  const [alerts, setAlerts] = useState(false);
  const field = cx("h-11 min-w-0 flex-1 rounded-control border border-border bg-surface-2 px-space-2 text-t13 text-fg", !touch && "md:h-9", focus);
  const chip = (on: boolean) => cx("inline-flex shrink-0 items-center gap-space-1 rounded-control border px-space-2 text-t13 font-semibold",
    touch ? "min-h-11" : "min-h-11 md:min-h-9", on ? "border-brand bg-surface-2 text-brand" : "border-border text-fg-muted hover:bg-surface-2", focus);
  const id = `watch-screen-${listKey.replace(/[^a-z0-9]/gi, "-")}`;
  const chosen = choice.screen_id != null;
  const unusable = state.screen && !state.screen.usable ? state.screen.why : null;
  return (
    <div data-watch-screen={listKey} className="space-y-space-1 border-b border-subtle px-space-2 py-space-2">
      <div className="flex items-center gap-space-1">
        <label htmlFor={id} className="shrink-0 px-space-1 text-t12 font-semibold text-fg-muted">Screen</label>
        <select id={id} value={choice.screen_id ?? ""} className={field} data-testid="watch-screen-pick"
          onChange={(e) => onChoose(e.target.value ? { ...NO_CHOICE, ...choice, screen_id: Number(e.target.value) } : NO_CHOICE)}>
          <option value="">None</option>
          {screens.map((s) => <option key={s.id} value={s.id} disabled={!s.usable}>{s.name}{s.usable ? "" : " (not for lists)"}</option>)}
        </select>
      </div>
      {chosen && !unusable && (
        <div className="flex flex-wrap items-center gap-space-1">
          <p className="flex min-w-0 flex-1 flex-wrap items-center gap-x-space-2 px-space-1 text-t12 text-fg-muted" role="status" data-testid="watch-screen-count">
            <span className="whitespace-nowrap font-semibold text-fg tabular-nums">{countText(items, state.matched)}</span>
            {state.checking && <span>Checking…</span>}
            {state.outcome?.slowed && <span className="text-warn">Slowed by broker limit</span>}
            {state.failed && <span className="text-fg">Could not be checked just now. It tries again at the next bar.</span>}
          </p>
          <button type="button" aria-pressed={choice.matches_only} onClick={() => onChoose({ ...choice, matches_only: !choice.matches_only })}
            className={chip(choice.matches_only)} data-testid="watch-screen-only">
            <Filter size={14} aria-hidden />Matches only
          </button>
          <button type="button" aria-expanded={alerts} aria-label="Alert on new matches" title="Alert on new matches"
            onClick={() => setAlerts((v) => !v)} className={chip(choice.alert)} data-testid="watch-screen-alerts">
            <Bell size={14} aria-hidden /><span>{choice.alert ? "On" : "Off"}</span>
          </button>
        </div>
      )}
      {unusable && <p className="px-space-1 text-t12 text-fg-muted">{unusable}</p>}
      {chosen && alerts && (
        <fieldset className="space-y-space-1 rounded-control border border-subtle px-space-2 py-space-1 text-t13" data-testid="watch-screen-alert-box">
          <legend className="px-space-1 text-t12 font-semibold text-fg-muted">When a stock newly meets this screen</legend>
          <label className={cx("flex items-center gap-space-2", touch ? "min-h-11" : "min-h-11 md:min-h-9")}>
            <input type="checkbox" checked={choice.alert} onChange={(e) => onChoose({ ...choice, alert: e.target.checked, telegram: e.target.checked && choice.telegram })}
              className="h-4 w-4 accent-brand" data-testid="watch-screen-alert" />
            Alert me in the app
          </label>
          <label className={cx("flex items-center gap-space-2", touch ? "min-h-11" : "min-h-11 md:min-h-9", !choice.alert && "opacity-50")}>
            <input type="checkbox" checked={choice.telegram} disabled={!choice.alert} onChange={(e) => onChoose({ ...choice, telegram: e.target.checked })}
              className="h-4 w-4 accent-brand" data-testid="watch-screen-telegram" />
            Also on my Telegram
          </label>
          <p className="text-t12 text-fg-muted">Checked while this list is open, when a new bar closes.</p>
        </fieldset>
      )}
      {error && <p role="alert" className="px-space-1 text-t12 text-fg">{error}</p>}
    </div>
  );
}
