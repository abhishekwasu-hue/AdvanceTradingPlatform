import { lazy, type ComponentType, type LazyExoticComponent } from "react";
import type { Page } from "../components/Sidebar";
import type { PageProps } from "../routes";

/**
 * UI v2 proposal §12: "the router picks `PageV2 ?? Page` per page", so a page switches to its v2 layout on its own and
 * a half-finished v2 never shows a broken page. Only the v2 shell (AppShellV2, `ui_v2` on) reads this map; with the flag
 * off every page renders exactly as today. Each v2 page is its own lazy chunk.
 */
export const PAGE_V2: Partial<Record<Page, LazyExoticComponent<ComponentType<PageProps>>>> = {
  signals: lazy(() => import("../signals/SignalsV2")),          // UI2-3: the Signals inbox (§7)
};
