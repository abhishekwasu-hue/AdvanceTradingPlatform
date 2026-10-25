import { TabRow, type SubTabsProps } from "./SubTabs";

/**
 * UI-H Wave 0, W0-2 (owner, 24 Oct): the PRIMARY tab row - page-level navigation directly under the top bar (My trades
 * / Strategies / Performance, Admin sections, Sectors / Indices, Signals, Learn, the symbol hub). An underline bar, so
 * it reads clearly different from the filled SubTabs used for choices inside the content. Same kinds, keys, fades and
 * no-wrap rule as SubTabs (src/ui/SubTabs.tsx): equal widths up to 5 tabs on a phone, one scrolling row beyond.
 */
export function PageTabs<K extends string>(props: SubTabsProps<K>) {
  return <TabRow {...props} level="primary" />;
}
