import { useCallback, useEffect, useLayoutEffect, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { cx } from "../components/primitives/cx";

/**
 * UI-H Wave 0 (H2; owner decisions 24 Oct: one navigation style, then W0-1 and W0-2): the tab rows of the app come in
 * TWO levels that look clearly different, both built here so they behave the same:
 * - PageTabs (src/ui/PageTabs.tsx, `level="primary"`): page-level navigation directly under the top bar (My trades /
 *   Strategies / Performance, Admin sections, Sectors / Indices) - an underline tab bar;
 * - SubTabs (`level="secondary"`, the default): a choice inside the content (Day / Week / Month, the Analyses sections) -
 *   the filled segmented control: a recessed track (surface-2), the active tab filled with the accent (`bg-brand` =
 *   the brand fill token, `text-on-brand`, bold - WCAG AA in both themes, see SubTabs.test.tsx), inactive tabs muted.
 *
 * W0-1, never a wrapped row:
 * - up to 5 options: equal widths across the row;
 * - more than 5 (or labels too long for the screen): ONE row that scrolls sideways, the active tab scrolled into view
 *   and a fade on the side(s) where more tabs sit.
 * Every target is 44 px tall on a phone.
 *
 * Semantics (`kind`):
 * - "tabs" (default): role="tablist" / role="tab" + aria-selected, roving focus (only the active tab is in the Tab
 *   order; ← → Home End move and select). With `idPrefix` each tab is `<prefix>-tab-<key>` and controls
 *   `<prefix>-panel-<key>` - the page renders that tabpanel.
 * - "links": a <nav> of links, the current one aria-current="page" (each tab has its own address). `onChange`, when
 *   given, is called instead of following the link (a page that keeps the tab in its query string).
 * - "toggle": role="group" of aria-pressed buttons, for a row that picks a view of the same content (timeframes,
 *   Day / Week / Month). Same look, same keys.
 */
export interface SubTabItem<K extends string = string> {
  key: K;
  label: ReactNode;
  /** A shorter label for phones (below md). */
  short?: ReactNode;
  /** The accessible name when the label is not plain text (or differs on a phone). */
  ariaLabel?: string;
  /** "links" only: the address of this tab. */
  to?: string;
  title?: string;
  /** Extra data-* attributes for this tab (tests and tours). */
  data?: Record<string, string>;
}

export interface SubTabsProps<K extends string = string> {
  items: readonly SubTabItem<K>[];
  value: K | null;
  onChange?: (key: K) => void;
  /** The accessible name of the row ("Analyses", "Timeframe"). */
  label: string;
  kind?: "tabs" | "links" | "toggle";
  /** "auto" (default): equal widths up to 5 options, a scrolling row above. "scroll": always the scrolling row. */
  layout?: "auto" | "scroll";
  /** "md" (default): 44 px on a phone, 36 px from md. "sm": a toolbar row (same phone size, 32 px from md). */
  size?: "md" | "sm";
  /** "tabs": ids `<prefix>-tab-<key>` and aria-controls `<prefix>-panel-<key>`. */
  idPrefix?: string;
  /** Kept for callers from the first cut: equal widths are now the default up to 5 options. */
  full?: boolean;
  className?: string;
  /** Extra data-* attributes for the row. */
  data?: Record<string, string>;
  /** Extra content at the end of the row, inside the group (the timeframes' More menu). */
  trailing?: ReactNode;
}

/** The most options shown as equal-width segments; more become one scrolling row (W0-1). */
export const MAX_SEGMENTS = 5;

/** Secondary (SubTabs) classes, exported for the contrast and guard tests. */
export const SUBTAB_ACTIVE = "bg-brand font-semibold text-on-brand shadow-card";
export const SUBTAB_IDLE = "font-medium text-fg-muted hover:bg-surface-1 hover:text-fg";
export const SUBTAB_TRACK = "rounded-panel bg-surface-2 p-space-1";
/** Primary (PageTabs) classes: an underline bar. */
export const PAGETAB_ACTIVE = "border-brand font-semibold text-fg";
export const PAGETAB_IDLE = "border-transparent font-medium text-fg-muted hover:text-fg";
export const PAGETAB_TRACK = "border-b border-subtle";

function dataAttrs(data?: Record<string, string>): Record<string, string> {
  if (!data) return {};
  return Object.fromEntries(Object.entries(data).map(([k, v]) => [k.startsWith("data-") ? k : `data-${k}`, v]));
}

function tabEl(row: HTMLElement, key: string): HTMLElement | null {
  return Array.from(row.querySelectorAll<HTMLElement>("[data-subtab]")).find((el) => el.dataset.subtab === key) ?? null;
}

/** The key the arrow / Home / End keys move to, or null for any other key. */
export function nextKey<K extends string>(keys: readonly K[], current: K | null, key: string): K | null {
  if (keys.length === 0) return null;
  const at = current === null ? -1 : keys.indexOf(current);
  switch (key) {
    case "ArrowRight": case "ArrowDown": return keys[(at + 1) % keys.length];
    case "ArrowLeft": case "ArrowUp": return keys[(at - 1 + keys.length) % keys.length];
    case "Home": return keys[0];
    case "End": return keys[keys.length - 1];
    default: return null;
  }
}

/** "segments" (equal widths) up to MAX_SEGMENTS options, else "scroll" (exported for tests). */
export function rowMode(count: number, layout: "auto" | "scroll" = "auto"): "segments" | "scroll" {
  return layout === "scroll" || count > MAX_SEGMENTS ? "scroll" : "segments";
}

/** Which sides of a sideways-scrolling row hide more tabs. */
export function fadeSides(scrollLeft: number, clientWidth: number, scrollWidth: number): { left: boolean; right: boolean } {
  return { left: scrollLeft > 2, right: scrollLeft + clientWidth < scrollWidth - 2 };
}

const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

/** The row's fades and keeping the active tab in view (both levels). */
function useScrollRow(value: string | null) {
  const rowRef = useRef<HTMLDivElement>(null);
  const [fade, setFade] = useState({ left: false, right: false });
  const measure = useCallback(() => {
    const row = rowRef.current;
    if (!row) return;
    const next = fadeSides(row.scrollLeft, row.clientWidth, row.scrollWidth);
    setFade((f) => (f.left === next.left && f.right === next.right ? f : next));
  }, []);
  useIsoLayoutEffect(() => {
    const row = rowRef.current;
    if (row && value !== null) {
      const el = tabEl(row, value);
      if (el) {
        const left = el.offsetLeft - row.offsetLeft;
        const right = left + el.offsetWidth;
        if (left < row.scrollLeft) row.scrollLeft = Math.max(0, left - 24);
        else if (right > row.scrollLeft + row.clientWidth) row.scrollLeft = right - row.clientWidth + 24;
      }
    }
    measure();
  }, [value, measure]);
  useEffect(() => {
    const row = rowRef.current;
    if (!row || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(measure);
    ro.observe(row);
    return () => ro.disconnect();
  }, [measure]);
  return { rowRef, fade, measure };
}

export function TabRow<K extends string>({
  items, value, onChange, label, kind = "tabs", layout = "auto", size = "md", idPrefix, className, data, trailing,
  level = "secondary",
}: SubTabsProps<K> & { level?: "primary" | "secondary" }) {
  const { rowRef, fade, measure } = useScrollRow(value);
  const primary = level === "primary";
  const mode = rowMode(items.length + (trailing ? 1 : 0), layout);

  const keys = items.map((i) => i.key);
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const next = nextKey(keys, value, e.key);
    if (!next) return;
    e.preventDefault();
    onChange?.(next);
    if (rowRef.current) tabEl(rowRef.current, next)?.focus();
  };

  const tabClass = (on: boolean) => cx(
    "inline-flex min-h-11 items-center justify-center whitespace-nowrap text-t14 transition-colors duration-fast",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
    mode === "segments" ? "flex-1 basis-0" : "shrink-0",
    primary
      ? cx("-mb-px border-b-2 px-space-3", mode === "segments" && "md:flex-none md:basis-auto", on ? PAGETAB_ACTIVE : PAGETAB_IDLE)
      : cx("rounded-control px-space-3 focus-visible:ring-offset-1", size === "sm" ? "md:min-h-8 md:px-space-2" : "md:min-h-9",
        on ? SUBTAB_ACTIVE : SUBTAB_IDLE),
  );

  const body = (it: SubTabItem<K>) => it.short === undefined ? it.label : (
    <>
      <span className="md:hidden">{it.short}</span>
      <span className="hidden md:inline">{it.label}</span>
    </>
  );

  const track = cx(
    "subtabs-scroll flex max-w-full overflow-x-auto",
    primary ? cx(PAGETAB_TRACK, "gap-space-1 md:gap-space-4") : cx(SUBTAB_TRACK, "gap-space-1"),
  );
  // The fade takes the colour of what is under the row: the track for SubTabs, the page for PageTabs.
  const fadeFrom = primary ? "from-surface" : "from-surface-2";
  const fades = (
    <>
      {fade.left && <span aria-hidden data-fade="left" className={cx("pointer-events-none absolute inset-y-0 left-0 z-10 w-8 bg-gradient-to-r to-transparent", fadeFrom, !primary && "rounded-l-panel")} />}
      {fade.right && <span aria-hidden data-fade="right" className={cx("pointer-events-none absolute inset-y-0 right-0 z-10 w-8 bg-gradient-to-l to-transparent", fadeFrom, !primary && "rounded-r-panel")} />}
    </>
  );
  const wrap = cx("relative min-w-0", primary ? "w-full" : mode === "segments" ? "w-full md:w-fit" : "w-full", className);

  if (kind === "links") {
    return (
      <nav aria-label={label} data-subtabs="links" data-level={level} data-mode={mode} className={wrap} {...dataAttrs(data)}>
        <div ref={rowRef} className={track} onKeyDown={onKeyDown} onScroll={measure}>
          {items.map((it) => {
            const on = it.key === value;
            return (
              <Link key={it.key} to={it.to ?? "#"} aria-current={on ? "page" : undefined} aria-label={it.ariaLabel} title={it.title}
                data-subtab={it.key} data-active={on ? "true" : undefined} {...dataAttrs(it.data)}
                onClick={onChange ? (e) => { e.preventDefault(); onChange(it.key); } : undefined}
                className={tabClass(on)}>
                {body(it)}
              </Link>
            );
          })}
          {trailing}
        </div>
        {fades}
      </nav>
    );
  }

  const tabs = kind === "tabs";
  return (
    <div className={wrap}>
      <div ref={rowRef} role={tabs ? "tablist" : "group"} aria-label={label} data-subtabs={kind} data-level={level} data-mode={mode}
        {...dataAttrs(data)} className={track} onKeyDown={tabs ? onKeyDown : undefined} onScroll={measure}>
        {items.map((it) => {
          const on = it.key === value;
          const roving = tabs ? { tabIndex: on || (value === null && it.key === keys[0]) ? 0 : -1 } : {};
          return (
            <button key={it.key} type="button" title={it.title} aria-label={it.ariaLabel}
              {...(tabs
                ? { role: "tab", "aria-selected": on, id: idPrefix ? `${idPrefix}-tab-${it.key}` : undefined,
                    "aria-controls": idPrefix ? `${idPrefix}-panel-${it.key}` : undefined }
                : { "aria-pressed": on })}
              {...roving}
              data-subtab={it.key} data-active={on ? "true" : undefined} {...dataAttrs(it.data)}
              onClick={() => onChange?.(it.key)}
              className={tabClass(on)}>
              {body(it)}
            </button>
          );
        })}
        {trailing}
      </div>
      {fades}
    </div>
  );
}

/** The secondary row: a choice inside the content (the filled segmented control, or the scrolling row). */
export function SubTabs<K extends string>(props: SubTabsProps<K>) {
  return <TabRow {...props} level="secondary" />;
}

/** The class of one secondary tab, for a control that sits in a SubTabs row (the timeframes' More button). */
export function subTabClass(on: boolean, mode: "segments" | "scroll", size: "md" | "sm" = "md"): string {
  return cx(
    "inline-flex min-h-11 items-center justify-center gap-space-1 whitespace-nowrap rounded-control px-space-3 text-t14 transition-colors duration-fast",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-1",
    mode === "segments" ? "flex-1 basis-0" : "shrink-0",
    size === "sm" ? "md:min-h-8 md:px-space-2" : "md:min-h-9",
    on ? SUBTAB_ACTIVE : SUBTAB_IDLE,
  );
}
