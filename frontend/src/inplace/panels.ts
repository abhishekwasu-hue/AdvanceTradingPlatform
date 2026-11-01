import { BROKER_PATH } from "../broker/paths";
import { SETTINGS_PATH } from "../settings/paths";
import { chartTabPath, chartsSymbolFromPath } from "../shell/shellRoutes";
import { createRegistry, type PanelEntry, type PanelParams } from "./registry";

/**
 * NAV-1 (docs/design/M75_NO_NAVIGATION.md section 4.1): the first three in-place panels - a chart, the broker and the
 * AI key. Each reuses the existing component; it loads in its own chunk the first time the panel opens. The area PRs
 * (NAV-2..8) add their entries here.
 */

const clean = (pathname: string) => pathname.replace(/\/+$/, "") || "/";
const only = (o: Record<string, string | null | undefined>): PanelParams =>
  Object.fromEntries(Object.entries(o).filter((e): e is [string, string] => !!e[1]));

/** A chart: `/charts/NIFTY?tf=15min&exchange=BSE`, or the classic chart window's `?chart=NIFTY&tf=5min`. */
export const CHART_PANEL: PanelEntry = {
  kind: "chart",
  keys: ["symbol", "tf", "exchange"],
  title: (p) => p.symbol ?? "Chart",
  presentation: "side-wide",
  match: ({ pathname, search }) => {
    const fromPath = chartsSymbolFromPath(pathname);
    const symbol = fromPath ?? search.get("chart")?.trim().toUpperCase() ?? null;
    if (!symbol) return null;
    return only({ symbol, tf: search.get("tf"), exchange: search.get("exchange")?.trim().toUpperCase() });
  },
  href: (p) => chartTabPath(p.symbol ?? "", p.exchange, p.tf),
  load: () => import("./panels/ChartPanel"),
};

/** The broker: connect, renew or remove (the Broker page's own body). */
export const BROKER_PANEL: PanelEntry = {
  kind: "broker",
  keys: [],
  title: () => "Broker",
  presentation: "drawer",
  match: ({ pathname }) => (clean(pathname) === BROKER_PATH || clean(pathname) === "/settings/broker" ? {} : null),
  href: () => BROKER_PATH,
  load: () => import("./panels/BrokerPanel"),
};

/** The user's own AI key (only while `ai_one_key` is on; otherwise the link goes to Settings as today). */
export const AI_KEY_PANEL: PanelEntry = {
  kind: "ai-key",
  keys: [],
  title: () => "Connect AI",
  presentation: "modal",
  match: ({ pathname }) => (clean(pathname) === SETTINGS_PATH.ai ? {} : null),
  href: () => SETTINGS_PATH.ai,
  enabled: (on) => on("ai_one_key"),
  load: () => import("./panels/AiKeyPanel"),
};

export const DEFAULT_PANELS: readonly PanelEntry[] = [CHART_PANEL, BROKER_PANEL, AI_KEY_PANEL];
export const DEFAULT_REGISTRY = createRegistry(DEFAULT_PANELS);
