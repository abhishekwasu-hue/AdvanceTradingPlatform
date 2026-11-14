import { Ellipsis, RotateCcw, Save } from "lucide-react";
import type { ReactNode } from "react";
import { Combobox, type ComboOption } from "../ui/Combobox";
import { cx } from "../components/primitives/cx";

/** A square icon button: 44 px on a touch screen, 36 px with a mouse; its name is its tooltip and its aria-label. */
export const ICON_BUTTON = "inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-control border border-border bg-surface-1 text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50 md:h-9 md:w-9";

const MENU_ITEM = "flex min-h-11 w-full items-center rounded-control px-space-3 text-left text-t14 text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50 md:min-h-9";

/** The look of a "Panels" summary inside the More menu (a menu row with its icon). */
export const MENU_SUMMARY = cx(MENU_ITEM, "cursor-pointer list-none gap-space-2 [&::-webkit-details-marker]:hidden");

/**
 * Owner 14 Nov ("space waste hot aahe, important panel khali dablet"; then "Default yes, menu mdhe halwa, anavashyak goshti
 * padadya mage lapva"): the Terminal's layout controls as the smallest group in the context bar's row - only the layout
 * picker and a "More" (⋯) menu stay on screen. The menu holds Panels, Save (as mine), Save as new, Rename, Delete, Reset
 * and "N of 10 saved". Same actions and the same `data-terminal` hooks as before (open the menu, then the item).
 */
export function LayoutToolsV2({ value, options, onPick, busy, own, dirty, count, max, panels, onSave, onSaveAs, onRename, onDelete, onReset,
  resetTitle, align = "right" }: {
  value: string; options: ComboOption[]; onPick: (id: string) => void; busy: boolean;
  /** His own saved layout's name when one is in use (Save / Save as new / Rename / Delete), else null (Save as mine). */
  own: string | null; dirty: boolean; count: number; max: number;
  /** The page's Panels menu (a `<details data-terminal="panels">` styled with MENU_SUMMARY), shown first in the More menu. */
  panels: ReactNode;
  onSave: () => void; onSaveAs: () => void; onRename: () => void; onDelete: () => void; onReset: () => void;
  resetTitle: string;
  /** Which edge the menu opens from: "right" when the group sits at the row's right end. */
  align?: "left" | "right";
}) {
  const saveName = own ? "Save" : "Save as mine";
  const close = (e: { currentTarget: HTMLElement }) => e.currentTarget.closest("details[data-terminal='more']")?.removeAttribute("open");
  return (
    <div className="flex items-center gap-space-1" data-tour="terminal-layouts">
      <div title="Layout" className="min-w-0">
        <Combobox label="Layout" value={value} onChange={onPick} disabled={busy} dataAttrs={{ "data-terminal": "layout" }} options={options} triggerClassName="!gap-space-1 !px-space-2" />
      </div>
      <details className="relative" data-terminal="more">
        <summary className={cx(ICON_BUTTON, "cursor-pointer list-none [&::-webkit-details-marker]:hidden")} aria-label="More layout actions"
          title="Panels, save, reset">
          <Ellipsis size={16} aria-hidden />
        </summary>
        <div className={cx("absolute z-20 mt-space-1 w-56 space-y-px rounded-panel border border-subtle bg-surface-1 p-space-1 shadow-raised",
          align === "right" ? "right-0" : "left-0")} data-terminal="menu">
          {panels}
          <button type="button" className={cx(MENU_ITEM, "gap-space-2")} onClick={(e) => { close(e); onSave(); }} disabled={busy || (!!own && !dirty)}
            data-terminal="save" aria-label={saveName} title={saveName}>
            <Save size={16} aria-hidden />{saveName}
          </button>
          {own && <>
            <button type="button" className={MENU_ITEM} onClick={(e) => { close(e); onSaveAs(); }} disabled={busy || count >= max} data-terminal="save-as">Save as new</button>
            <button type="button" className={MENU_ITEM} onClick={(e) => { close(e); onRename(); }} disabled={busy} data-terminal="rename">Rename</button>
            <button type="button" className={MENU_ITEM} onClick={(e) => { close(e); onDelete(); }} disabled={busy} data-terminal="delete">Delete</button>
          </>}
          <button type="button" className={cx(MENU_ITEM, "gap-space-2")} onClick={(e) => { close(e); onReset(); }} data-terminal="reset" aria-label="Reset"
            title={resetTitle}>
            <RotateCcw size={16} aria-hidden />Reset
          </button>
          <p className="border-t border-subtle px-space-3 py-space-2 text-t13 text-fg-muted" data-terminal="count">{count} of {max} saved</p>
        </div>
      </details>
    </div>
  );
}
