# Owner orders M57 + M58 (30 Oct 2026, verbatim)

M57 · ATP · Sidebar show/hide (U7, inside M49)
Finding (code, main 56d7b64): shell/SidebarV2.tsx:100-101 — the sidebar is a fixed 240 px (`w-60`) aside, `hidden` below md and always open at md+; there is no toggle anywhere in shell/ (AppShellV2.tsx, TopBarV2.tsx) and no persisted state. So on desktop/tablet the menu cannot be hidden and on phone it cannot be shown at all (phone relies on the bottom bar + More).
U7-1 Add one toggle in the top bar (left of the title; icon PanelLeft / PanelLeftClose, aria-label "Hide menu" / "Show menu", keyboard shortcut `[`): md+ collapses the sidebar to a 56 px icon rail (icons + tooltips, logo, dot), expands back; state persisted per user (localStorage key + settings sync like the density setting). Default: expanded on ≥ lg, rail on md.
U7-2 Phone: the same button opens the full navigation as a sheet (the existing MoreSheetV2 list, all sections) so every page is reachable from the top bar too; bottom bar unchanged.
U7-3 The content area takes the freed width (chart/Terminal panels re-layout); title never wraps (M49 C2 fix applies).
U7-4 Tests: toggle persists across reload; rail shows every item with tooltip; focus returns to the button; proof = before/after screenshots at 390, 768, 1280 px.

M58 · ATP · Terminal panel resizing must be obvious and push neighbours (T4, inside M49/M39)
Finding (code, main 56d7b64): resizing exists but is unusable in practice — terminal/TerminalGridV2.tsx:114-115 the only handle is a 16 px corner square at the bottom-right of each panel (below the fold on tall panels, no affordance on hover, no cursor hint on edges); layoutModel resizePanel REFUSES any resize that would overlap a neighbour, so in the default "Option seller" layout (Chart | Chain | Basket filling 12 columns) widening any panel is silently refused — nothing moves, no message. Keyboard: Shift+arrows only via the move button (:104). No panel-size setting in the "Panels" dialog.
T4-1 Gutters: a draggable splitter on every shared edge between adjacent panels (vertical between columns, horizontal between rows), full-length, 6-8 px hit area, cursor col-resize/row-resize, highlighted on hover; dragging a gutter GROWS one panel and SHRINKS the neighbour(s) on that edge (down to their minW/minH), never refused silently. Keep the corner handle only as a secondary way.
T4-2 Refusal feedback: when a size/move cannot be applied (min size reached, grid edge), show the limit inline (a short toast or the gutter turns amber) instead of doing nothing.
T4-3 Panels dialog: per open panel a width (columns) and height (rows) stepper with the min shown; "Fit to screen" and "Equal widths" buttons; current sizes shown in the dialog; changes apply live and are saved with "Save as mine".
T4-4 Double-click a gutter = reset the two panels to the preset sizes; double-click a panel header = maximise/restore that panel (others collapse to headers).
T4-5 Phone/tablet unchanged (tabs); from 1024 px the gutters apply. Chain panel: when narrowed below its minW, hide IV/OI change columns first (column priority), never cut text mid-word.
T4-6 Tests: gutter drag changes both neighbours; min sizes respected with feedback; dialog steppers persist; double-click behaviours; proof = short screen recording or before/after screenshots at 1280 and 1920 px.
