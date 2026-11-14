import { Ellipsis, RotateCcw, Save } from "lucide-react";
import type { ReactNode } from "react";
import { Combobox, type ComboOption } from "../ui/Combobox";
import { cx } from "../components/primitives/cx";

/** A square icon button: 44 px on a touch screen, 36 px with a mouse; its name is its tooltip and its aria-label. */
export const ICON_BUTTON = "inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-control border border-border bg-surface-1 text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50 md:h-9 md:w-9";

const MENU_ITEM = "flex min-h-11 w-full items-center rounded-control px-space-3 text-left text-t14 text-fg hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand disabled:opacity-50 md:min-h-9";

/**
 * Owner 14 Nov ("space waste hot aahe, important panel khali dablet"): the Terminal's layout controls as one compact group
 * that sits in the context bar's row - the layout picker, then Panels, Save (as mine) and Reset as icon buttons (each
 * named by its tooltip and aria-label), and a "More" menu with "N of 10 saved" and, for his own layout, Save as new,
 * Rename and Delete. Same actions and the same `data-terminal` hooks as the old Layout row.
 */
export function LayoutToolsV2({ value, options, onPick, busy, own, dirty, count, max, panels, onSave, onSaveAs, onRename, onDelete, onReset,
  resetTitle, align = "right" }: {
  value: string; options: ComboOption[]; onPick: (id: string) => void; busy: boolean;
  /** His own saved layout's name when one is in use (Save / Save as new / Rename / Delete), else null (Save as mine). */
  own: string | null; dirty: boolean; count: number; max: number;
  /** The page's Panels menu (a `<details data-terminal="panels">`). */
  panels: ReactNode;
  onSave: () => void; onSaveAs: () => void; onRename: () => void; onDelete: () => void; onReset: () => void;
  resetTitle: string;
  /** Which edge the menus open from: "right" when the group sits at the row's right end. */
  align?: "left" | "right";
}) {
  const saveName = own ? "Save" : "Save as mine";
  const close = (e: { currentTarget: HTMLElement }) => e.currentTarget.closest("details")?.removeAttribute("open");
  return (
    <div className="flex flex-wrap items-center gap-space-1" data-tour="terminal-layouts">
      <div title="Layout" className="min-w-0">
        <Combobox label="Layout" value={value} onChange={onPick} disabled={busy} dataAttrs={{ "data-terminal": "layout" }} options={options} />
      </div>
      {panels}
      <button type="button" className={ICON_BUTTON} onClick={onSave} disabled={busy || (!!own && !dirty)} data-terminal="save"
        aria-label={saveName} title={saveName}>
        <Save size={16} aria-hidden />
      </button>
      <button type="button" className={ICON_BUTTON} onClick={onReset} data-terminal="reset" aria-label="Reset" title={resetTitle}>
        <RotateCcw size={16} aria-hidden />
      </button>
      <details className="relative" data-terminal="more">
        <summary className={cx(ICON_BUTTON, "cursor-pointer list-none [&::-webkit-details-marker]:hidden")} aria-label="More layout actions"
          title="More layout actions">
          <Ellipsis size={16} aria-hidden />
        </summary>
        <div className={cx("absolute z-20 mt-space-1 w-56 space-y-px rounded-panel border border-subtle bg-surface-1 p-space-1 shadow-raised",
          align === "right" ? "right-0" : "left-0")}>
          <p className="px-space-3 py-space-2 text-t13 text-fg-muted" data-terminal="count">{count} of {max} saved</p>
          {own && <>
            <button type="button" className={MENU_ITEM} onClick={(e) => { close(e); onSaveAs(); }} disabled={busy || count >= max} data-terminal="save-as">Save as new</button>
            <button type="button" className={MENU_ITEM} onClick={(e) => { close(e); onRename(); }} disabled={busy} data-terminal="rename">Rename</button>
            <button type="button" className={MENU_ITEM} onClick={(e) => { close(e); onDelete(); }} disabled={busy} data-terminal="delete">Delete</button>
          </>}
        </div>
      </details>
    </div>
  );
}
