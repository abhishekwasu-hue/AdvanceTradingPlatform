import type { ComponentType } from "react";
import { useLocation } from "react-router";
import { PAGE_COMPONENTS, type PageProps } from "../../routes";
import { PAGE_COMPONENTS_V2 } from "../../shell/pagesV2";
import { shellKeyFromPath } from "../../shell/shellRoutes";
import { RouteGate } from "./RouteGate";

/**
 * M75 NAV-5a: a page in place - the same component the v2 shell shows at the panel's own address (its v2 layout where
 * it has one, else today's page), behind the same broker gate. The panel's address (PanelRouter) is the full route with
 * its search and hash, so the page reads them exactly as on its own route.
 */
export default function PagePanel() {
  const { pathname } = useLocation();
  const key = shellKeyFromPath(pathname);
  const Page: ComponentType<PageProps> | undefined = key
    ? PAGE_COMPONENTS_V2[key] ?? (PAGE_COMPONENTS as Partial<Record<string, ComponentType<PageProps>>>)[key] : undefined;
  if (!Page) return null;
  return <RouteGate path={pathname}><Page /></RouteGate>;
}
