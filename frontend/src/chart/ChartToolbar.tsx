import { MoreHorizontal, Redo2, Undo2 } from "lucide-react";
import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { Link } from "react-router";
import { cx } from "../components/primitives/cx";
import { Combobox } from "../ui/Combobox";
import { SymbolPicker } from "../ui/SymbolPicker";
import { TimeframePicker, type TimeframePickerProps } from "../ui/TimeframePicker";
import { PHONE_QUERY, useMediaQuery } from "../utils/useMediaQuery";
import { ChartSettingsPopover } from "./ChartSettingsPopover";
import { CHART_TYPES_V2, CHART_TYPE_LABELS_V2, type ChartTypeV2 } from "./chartCore";
import { DrawingMenu, type DrawingToolsProps } from "./DrawingRail";
import { IndicatorsMenu } from "./IndicatorsMenu";
import type { IndicatorState, MenuGroup } from "./indicatorsModel";
import type { ChartPrefs, ChartPrefsPatch } from "./useChartPrefs";

/**
 * CT-1 C1 (one component per kind): the ONE chart toolbar. Every v2 chart's controls are drawn here, in one of two modes:
 * - "full": the Charts page's top bar - symbol, market state, timeframe, chart type, Indicators, the drawing menu with
 *   undo / redo, the host's actions (Read this chart, full screen, watchlist ...) and the chart settings. On a phone
 *   (CT-1 L1) it is one line - Symbol · Timeframe · Indicators · Draw · More - and everything else sits under More.
 * - "compact": a chart inside another page or a Multi-chart cell - the symbol (when the host lets it change), the
 *   timeframe, the chart type (cells), Indicators (only the groups the host allows: an embedded chart offers the
 *   price-pane lines alone), the chart settings and the host's small actions ("Open in Chart tab").
 * The pickers are the shared ones (SymbolPicker, TimeframePicker, IndicatorsMenu, DrawingMenu, ChartSettingsPopover);
 * a host passes data and callbacks, never its own toolbar markup.
 */

export interface ToolbarAction {
  id: string;
  /** The accessible name, and the row's words under More on a phone. */
  label: string;
  /** The tooltip (defaults to the label). */
  title?: string;
  icon: ReactNode;
  onClick?: () => void;
  /** A link instead of a button. */
  to?: string;
  pressed?: boolean;
  disabled?: boolean;
  haspopup?: "dialog";
  testId?: string;
  /** Under More on a phone, the row's words when they differ from the label ("Show the watchlist"). */
  moreLabel?: string;
}

export interface ChartTypeControl { id: string; label?: string; value: ChartTypeV2; onChange: (t: ChartTypeV2) => void; className?: string }
export interface IndicatorsControl {
  state: IndicatorState; on: (flag: string) => boolean; onToggle: (id: string) => void;
  /** The menu groups offered (default all). An embedded chart: ["popular"], the price-pane lines only. */
  groups?: readonly MenuGroup[];
}
export interface SettingsControl { prefs: ChartPrefs; onChange: (patch: ChartPrefsPatch) => void; error?: string | null }
export interface SymbolControl { value: string; exchange: string; onPick: Parameters<typeof SymbolPicker>[0]["onPick"]; storageKey: string | null; label?: string; className?: string }

export interface ChartToolbarProps {
  mode: "full" | "compact";
  /** The toolbar's accessible name ("Chart controls", "Chart controls for NIFTY 50"). */
  label: string;
  symbol?: SymbolControl;
  timeframe: TimeframePickerProps & { wrapperClassName?: string; tour?: string };
  chartType?: ChartTypeControl;
  indicators?: IndicatorsControl;
  /** The drawing tools (full mode): the phone's Draw menu and undo / redo. */
  drawing?: DrawingToolsProps;
  settings?: SettingsControl;
  /** Before everything (a Multi-chart cell's link-group button). */
  leading?: ReactNode;
  /** The market state chip (full mode). */
  status?: ReactNode;
  /** The host's actions before the chart settings, and after them. */
  actions?: readonly ToolbarAction[];
  endActions?: readonly ToolbarAction[];
  /** Tests / the comparison page: force the phone layout on or off (default: the screen's width). */
  phone?: boolean;
  className?: string;
  tour?: string;
}

const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
export const toolbarIconBtn = cx("inline-flex min-h-11 min-w-11 items-center justify-center rounded-control text-fg-muted hover:bg-surface-2 hover:text-fg disabled:opacity-40 disabled:hover:bg-transparent md:min-h-9 md:min-w-9", focus);
const moreRow = cx("flex min-h-11 w-full items-center gap-space-2 rounded-control px-space-2 text-left text-t14 text-fg hover:bg-surface-2 disabled:opacity-40 aria-pressed:font-semibold", focus);

function drawingActions(drawing: DrawingToolsProps | undefined): ToolbarAction[] {
  if (!drawing) return [];
  const { tools } = drawing;
  return [
    { id: "undo", label: "Undo drawing", title: "Undo (Ctrl+Z)", icon: <Undo2 size={16} aria-hidden />, disabled: !tools?.view?.canUndo, onClick: () => void tools?.controller?.undo() },
    { id: "redo", label: "Redo drawing", title: "Redo (Ctrl+Y)", icon: <Redo2 size={16} aria-hidden />, disabled: !tools?.view?.canRedo, onClick: () => void tools?.controller?.redo() },
  ];
}

/** One action as an icon button (or link) in the bar. */
function IconAction({ a }: { a: ToolbarAction }) {
  const common = { "aria-label": a.label, title: a.title ?? a.label, className: toolbarIconBtn, "data-testid": a.testId, "data-action": a.id };
  if (a.to) return <Link to={a.to} {...common}>{a.icon}</Link>;
  return (
    <button type="button" {...common} disabled={a.disabled} aria-pressed={a.pressed} aria-haspopup={a.haspopup} onClick={a.onClick}>{a.icon}</button>
  );
}

/** One action as a labelled row under More (a phone). Picking it closes More. */
function MoreAction({ a, close }: { a: ToolbarAction; close: () => void }) {
  const body = <>{a.icon}<span>{a.moreLabel ?? a.label}</span></>;
  if (a.to) return <Link to={a.to} className={moreRow} data-testid={a.testId} data-action={a.id} onClick={close}>{body}</Link>;
  return (
    <button type="button" className={moreRow} data-testid={a.testId} data-action={a.id} disabled={a.disabled} aria-pressed={a.pressed}
      aria-haspopup={a.haspopup} onClick={() => { close(); a.onClick?.(); }}>{body}</button>
  );
}

/** The chart type as four buttons (under More on a phone: no list inside a menu). */
function ChartTypeButtons({ c }: { c: ChartTypeControl }) {
  return (
    <div role="radiogroup" aria-label={c.label ?? "Chart type"} className="grid grid-cols-4 gap-space-1 rounded-panel bg-surface-2 p-space-1">
      {CHART_TYPES_V2.map((t) => (
        <button key={t} type="button" role="radio" aria-checked={c.value === t} onClick={() => c.onChange(t)}
          className={cx("min-h-11 rounded-control px-space-1 text-t13", focus, c.value === t ? "bg-surface-1 font-semibold text-fg shadow-card" : "text-fg-muted hover:text-fg")}>
          {CHART_TYPE_LABELS_V2[t]}
        </button>
      ))}
    </div>
  );
}

/** The phone's More button and its panel (opens under the whole bar, across the screen's width). */
function MoreMenu({ children }: { children: (close: () => void) => ReactNode }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const ref = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !e.defaultPrevented) { setOpen(false); trigger.current?.focus(); } };
    // A popover opened from inside the panel (chart settings) is inside it; a sheet laid over the page is not "outside".
    const onDown = (e: PointerEvent) => {
      const t = e.target as Element | null;
      if (!ref.current || ref.current.contains(t) || t?.closest?.("[role='dialog'], [role='listbox'], [role='menu']")) return;
      setOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("pointerdown", onDown);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("pointerdown", onDown); };
  }, [open]);
  const close = () => setOpen(false);
  return (
    <div ref={ref} className="inline-flex">
      <button ref={trigger} type="button" aria-label="More chart controls" title="More" aria-haspopup="dialog" aria-expanded={open}
        aria-controls={open ? id : undefined} onClick={() => setOpen((o) => !o)} className={toolbarIconBtn} data-more-button>
        <MoreHorizontal size={18} aria-hidden />
      </button>
      {open && (
        <div id={id} role="dialog" aria-label="More chart controls" data-more-panel
          className="absolute inset-x-space-2 top-full z-30 mt-space-1 space-y-space-2 rounded-panel border border-subtle bg-surface-1 p-space-2 text-t14 text-fg shadow-raised">
          {children(close)}
        </div>
      )}
    </div>
  );
}

export function ChartToolbar(props: ChartToolbarProps) {
  const screenPhone = useMediaQuery(PHONE_QUERY);
  const phone = props.phone ?? screenPhone;
  if (props.mode === "compact") return <CompactBar {...props} />;
  return phone ? <PhoneBar {...props} /> : <FullBar {...props} />;
}

/** Full mode from 768 px: the Charts page's top bar as it was (CT-1 L1: the desktop is unchanged). */
function FullBar({ label, symbol, timeframe, chartType, indicators, drawing, settings, status, actions = [], endActions = [], tour, className }: ChartToolbarProps) {
  const { wrapperClassName, tour: tfTour, ...tf } = timeframe;
  return (
    <div role="toolbar" aria-label={label} data-chart-toolbar="full" data-tour={tour}
      className={cx("relative flex flex-wrap items-center gap-x-space-2 gap-y-space-1 border-b border-subtle p-space-2", className)}>
      {symbol && <SymbolPicker value={symbol.value} exchange={symbol.exchange} onPick={symbol.onPick} storageKey={symbol.storageKey} label={symbol.label}
        className={symbol.className ?? "w-44 min-w-[8.5rem] flex-none"} />}
      {status}
      <div className={wrapperClassName ?? "w-auto"} data-tour={tfTour}><TimeframePicker {...tf} /></div>
      {chartType && (
        <Combobox id={chartType.id} label={chartType.label ?? "Chart type"} value={chartType.value} onChange={(v) => chartType.onChange(v as ChartTypeV2)}
          options={CHART_TYPES_V2.map((t) => ({ value: t, label: CHART_TYPE_LABELS_V2[t] }))} className={chartType.className ?? "w-auto"} />
      )}
      {indicators && <div data-tour="charts-indicators"><IndicatorsMenu state={indicators.state} on={indicators.on} onToggle={indicators.onToggle} groups={indicators.groups} /></div>}
      <span className="ml-auto inline-flex items-center">
        {drawing && <DrawingMenu {...drawing} />}
        {drawingActions(drawing).map((a) => <IconAction key={a.id} a={a} />)}
        {actions.map((a) => <IconAction key={a.id} a={a} />)}
        {settings && <ChartSettingsPopover prefs={settings.prefs} onChange={settings.onChange} error={settings.error} />}
        {endActions.map((a) => <IconAction key={a.id} a={a} />)}
      </span>
    </div>
  );
}

/** Full mode on a phone (CT-1 L1): one line - Symbol · Timeframe · Indicators · Draw · More; the rest under More. */
function PhoneBar({ label, symbol, timeframe, chartType, indicators, drawing, settings, status, actions = [], endActions = [], tour, className }: ChartToolbarProps) {
  const { tour: tfTour } = timeframe;
  const rest = [...drawingActions(drawing), ...actions, ...endActions];
  const hasMore = rest.length > 0 || !!chartType || !!settings || !!status;
  return (
    <div role="toolbar" aria-label={label} data-chart-toolbar="phone" data-tour={tour}
      className={cx("relative flex items-center border-b border-subtle px-space-1 py-space-2", className)}>
      {symbol && <SymbolPicker value={symbol.value} exchange={symbol.exchange} onPick={symbol.onPick} storageKey={symbol.storageKey} label={symbol.label}
        className="mr-space-1 min-w-0 flex-1" />}
      <div className="shrink-0" data-tour={tfTour}><TimeframePicker {...timeframe} compact maxFavourites={0} /></div>
      {indicators && <div className="inline-flex" data-tour="charts-indicators"><IndicatorsMenu state={indicators.state} on={indicators.on} onToggle={indicators.onToggle} groups={indicators.groups} iconOnly spread /></div>}
      {drawing && <DrawingMenu {...drawing} />}
      {hasMore && (
        <MoreMenu>
          {(close) => (
            <>
              {status && <div className="flex items-center gap-space-2 px-space-1">{status}</div>}
              {chartType && <ChartTypeButtons c={chartType} />}
              {rest.length > 0 && (
                <div className="grid grid-cols-2 gap-space-1">
                  {rest.map((a) => <MoreAction key={a.id} a={a} close={close} />)}
                </div>
              )}
              {settings && (
                <div className="flex items-center justify-between gap-space-2 border-t border-subtle px-space-1 pt-space-2">
                  <span className="text-t13 text-fg-muted">Chart settings</span>
                  <ChartSettingsPopover prefs={settings.prefs} onChange={settings.onChange} error={settings.error} />
                </div>
              )}
            </>
          )}
        </MoreMenu>
      )}
    </div>
  );
}

/** The element's width, followed as it changes (null before the first measure, or without ResizeObserver). */
function useWidth(ref: RefObject<HTMLElement | null>): number | null {
  const [width, setWidth] = useState<number | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => setWidth(Math.round(el.getBoundingClientRect().width)));
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return width;
}

/** Compact mode: an embedded chart or a Multi-chart cell - one short row, no More. */
function CompactBar({ label, symbol, timeframe, chartType, indicators, settings, leading, actions = [], endActions = [], className }: ChartToolbarProps) {
  const { wrapperClassName, maxFavourites: given } = timeframe;
  // A narrow host (a Terminal panel, a phone) keeps the bar on one line: while the bar wraps, one starred timeframe
  // fewer beside the list (the list itself is never cut off); a wider bar tries them all again. A host's own
  // `maxFavourites` wins.
  const ref = useRef<HTMLDivElement>(null);
  const width = useWidth(ref);
  const [cut, setCut] = useState<{ width: number; n: number } | null>(null);
  const fit = cut && width !== null && width <= cut.width ? cut.n : undefined;
  const own = given === undefined;
  const maxFavourites = given ?? fit;
  useLayoutEffect(() => {
    const bar = ref.current;
    if (!own || !bar || width === null) return;
    const kids = Array.from(bar.children) as HTMLElement[];
    if (!kids.some((k) => k.offsetTop > kids[0].offsetTop + 4)) return;
    const n = bar.querySelectorAll("[data-favourite]").length;
    if (n > 0) setCut({ width, n: n - 1 });
  }, [own, width, fit]);
  return (
    <div ref={ref} role="toolbar" aria-label={label} data-chart-toolbar="compact"
      className={cx("relative flex flex-wrap items-center gap-space-1 border-b border-subtle p-space-1", className)}>
      {leading}
      {symbol && <SymbolPicker value={symbol.value} exchange={symbol.exchange} onPick={symbol.onPick} storageKey={symbol.storageKey} label={symbol.label}
        className={symbol.className ?? "min-w-[7.5rem] flex-1"} />}
      <div className={wrapperClassName ?? (symbol ? "shrink-0" : "min-w-fit flex-1")}><TimeframePicker {...timeframe} maxFavourites={maxFavourites} /></div>
      {chartType && (
        <Combobox id={chartType.id} label={chartType.label ?? "Chart type"} value={chartType.value} onChange={(v) => chartType.onChange(v as ChartTypeV2)}
          options={CHART_TYPES_V2.map((t) => ({ value: t, label: CHART_TYPE_LABELS_V2[t] }))} className={chartType.className} />
      )}
      {indicators && <IndicatorsMenu state={indicators.state} on={indicators.on} onToggle={indicators.onToggle} groups={indicators.groups} iconOnly spread />}
      {actions.map((a) => <IconAction key={a.id} a={a} />)}
      {settings && <ChartSettingsPopover prefs={settings.prefs} onChange={settings.onChange} error={settings.error} />}
      {endActions.map((a) => <IconAction key={a.id} a={a} />)}
    </div>
  );
}
