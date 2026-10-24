import { useEffect, useState } from "react";
import { HUB_LABELS, hubApi, hubHref, hubTabs, isHubInfo, type HubApi, type HubCarry, type HubInfo, type HubTab } from "./hubModel";
import { PageTabs } from "../ui/PageTabs";

/**
 * FNO-3 (owner order section 1): the symbol hub's tab row - "Chart | Option chain | Futures | Overview" at the top of the
 * Charts page and the Option chain page (v2 only). Option chain and Futures show only when the instrument master lists
 * F&O for the symbol (`GET /option-chain/{symbol}/hub`). Each tab is a link that carries the symbol, the expiry and
 * the timeframe (src/fno/hubModel.ts `hubHref`), so switching tabs keeps them. While the facts are not known, or when
 * they cannot be read, there is no tab row: the page shows its own content exactly as before.
 * `HubTabsView` is the row without reads (rendered directly by the tests).
 */
const CACHE_MS = 5 * 60_000;
const cache = new Map<string, { at: number; info: HubInfo | null }>();

/** Clears the per-tab cache of hub facts (tests). */
export function clearHubCache(): void { cache.clear(); }

/** The hub facts of a symbol, read once per symbol for a few minutes (the master changes once a day). */
export function useHubInfo(symbol: string, api: Pick<HubApi, "info"> = hubApi): HubInfo | null {
  const key = symbol.trim().toUpperCase();
  const cached = cache.get(key);
  const fresh = cached && Date.now() - cached.at < CACHE_MS ? cached.info : undefined;
  const [state, setState] = useState<{ key: string; info: HubInfo | null }>({ key, info: fresh ?? null });
  useEffect(() => {
    if (!key) return undefined;
    const hit = cache.get(key);
    // Only a real change re-renders the page (no facts for this symbol is the state it starts in).
    const show = (info: HubInfo | null) => setState((prev) => (prev.key === key && prev.info === info ? prev : { key, info }));
    if (hit && Date.now() - hit.at < CACHE_MS) { show(hit.info); return undefined; }
    let alive = true;
    api.info(key)
      .then((info) => {
        const ok = isHubInfo(info) ? info : null;
        cache.set(key, { at: Date.now(), info: ok });
        if (alive) show(ok);
      })
      .catch(() => {                                                         // no facts: no tab row (fails closed)
        cache.set(key, { at: Date.now(), info: null });                      // and not asked again for a few minutes
        if (alive) show(null);
      });
    return () => { alive = false; };
  }, [key, api]);
  return state.key === key ? state.info : (fresh ?? null);
}


export function HubTabsView({ info, current, carry }: { info: HubInfo | null; current: HubTab; carry: HubCarry }) {
  const tabs = hubTabs(info);
  if (!info || tabs.length === 0) return null;
  return (
    <PageTabs kind="links" label={`${info.underlying} pages`} value={current} data={{ "hub-tabs": "" }}
      items={tabs.map((t) => ({ key: t, label: HUB_LABELS[t], to: hubHref(t, info, carry), data: { "hub-tab": t } }))} />
  );
}

export default function SymbolHubTabsV2({ symbol, current, carry, api }: {
  symbol: string; current: HubTab; carry: HubCarry; api?: Pick<HubApi, "info">;
}) {
  const info = useHubInfo(symbol, api);
  return <HubTabsView info={info} current={current} carry={carry} />;
}
