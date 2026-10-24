import { useEffect, useRef, type KeyboardEvent, type ReactNode } from "react";
import { Link } from "react-router";
import { cx } from "../components/primitives/cx";

/**
 * UI-H Wave 0 (H2; owner decision 3, 24 Oct): the ONE segmented control for every tab / sub-tab row in the app. Chips
 * stay only where they filter a list; a row that switches what the page shows is a SubTabs.
 *
 * Look (colour_v2 tokens only): a recessed track (surface-2); the active tab is filled with the accent (`bg-brand` =
 * the brand fill token, `text-on-brand`, bold - WCAG AA in both themes, see SubTabs.test.tsx); inactive tabs are
 * muted (`text-fg-muted`, AA on surface-2) and brighten on hover. Every target is 44 px tall on a phone.
 *
 * Phones: every tab stays reachable. `layout="wrap"` (the default) wraps the row, so nothing is ever off-screen;
 * `layout="scroll"` keeps one line (a long row such as timeframes) with a visible scroll bar and scrolls the active
 * tab into view whenever it changes.
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
  /** A shorter label for phones (below md). The full label is still the accessible name via `ariaLabel` if set. */
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
  layout?: "wrap" | "scroll";
  /** "md" (default): 44 px on a phone, 36 px from md. "sm": a toolbar row (same phone size, 32 px from md). */
  size?: "md" | "sm";
  /** "tabs": ids `<prefix>-tab-<key>` and aria-controls `<prefix>-panel-<key>`. */
  idPrefix?: string;
  /** Stretch the tabs to the full width (equal shares) on every screen. */
  full?: boolean;
  className?: string;
  /** Extra data-* attributes for the row. */
  data?: Record<string, string>;
}

/** The classes of one tab (exported for the guard and contrast tests). */
export const SUBTAB_ACTIVE = "bg-brand font-semibold text-on-brand shadow-card";
export const SUBTAB_IDLE = "font-medium text-fg-muted hover:bg-surface-1 hover:text-fg";
export const SUBTAB_TRACK = "rounded-panel bg-surface-2 p-space-1";

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

export function SubTabs<K extends string>({
  items, value, onChange, label, kind = "tabs", layout = "wrap", size = "md", idPrefix, full = false, className, data,
}: SubTabsProps<K>) {
  const rowRef = useRef<HTMLDivElement>(null);

  // Scroll layout: the active tab is always in view (a phone opening on "Monthly" sees it, not "1 min").
  useEffect(() => {
    if (layout !== "scroll" || value === null) return;
    const row = rowRef.current;
    const el = row ? tabEl(row, value) : null;
    if (!row || !el) return;
    const left = el.offsetLeft - row.offsetLeft;
    const right = left + el.offsetWidth;
    if (left < row.scrollLeft) row.scrollLeft = left - 8;
    else if (right > row.scrollLeft + row.clientWidth) row.scrollLeft = right - row.clientWidth + 8;
  }, [layout, value]);

  const keys = items.map((i) => i.key);
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const next = nextKey(keys, value, e.key);
    if (!next) return;
    e.preventDefault();
    onChange?.(next);
    if (rowRef.current) tabEl(rowRef.current, next)?.focus();
  };

  const tabClass = (on: boolean) => cx(
    "inline-flex min-h-11 items-center justify-center whitespace-nowrap rounded-control px-space-3 text-t14 transition-colors duration-fast",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-1",
    size === "sm" ? "md:min-h-8 md:px-space-2" : "md:min-h-9",
    full ? "min-w-0 flex-1" : layout === "scroll" ? "shrink-0" : "flex-auto md:flex-none",
    on ? SUBTAB_ACTIVE : SUBTAB_IDLE,
  );

  const body = (it: SubTabItem<K>) => it.short === undefined ? it.label : (
    <>
      <span className="md:hidden">{it.short}</span>
      <span className="hidden md:inline">{it.label}</span>
    </>
  );

  const track = cx(
    "flex gap-space-1", SUBTAB_TRACK,
    layout === "scroll" ? "subtabs-scroll max-w-full overflow-x-auto" : "flex-wrap",
    full ? "w-full" : "w-full md:w-fit",
    className,
  );

  if (kind === "links") {
    return (
      <nav aria-label={label} data-subtabs="links" {...dataAttrs(data)}>
        <div ref={rowRef} className={track} onKeyDown={onKeyDown}>
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
        </div>
      </nav>
    );
  }

  const tabs = kind === "tabs";
  return (
    <div ref={rowRef} role={tabs ? "tablist" : "group"} aria-label={label} data-subtabs={kind} {...dataAttrs(data)}
      className={track} onKeyDown={tabs ? onKeyDown : undefined}>
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
    </div>
  );
}
