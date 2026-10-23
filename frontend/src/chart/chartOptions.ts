/**
 * Chart review R1 (a) + R4 (docs/design/CHART_COLOUR_REVIEW.md): the one builder of the theme-neutral chart options -
 * scroll, scale, kinetic scroll and the axis basics - used by the v2 `ChartCore` and by ProChart's `baseOptions`, so the
 * two charts can no longer drift apart. Colours stay with each chart's theme code (`chartTheme.ts`, ProChart COLORS).
 *
 * The gesture options have ONE owner: whoever holds the `InteractionState` (the core, or ProChart through
 * `LightweightEngine`'s default). Every change is written as the full sub-option objects this builder makes, never as
 * the booleans `handleScroll: true` / `handleScale: true` - the library expands a boolean into every sub-option, which
 * turned `vertTouchDrag` on after the first drawing drag (R4).
 */

/** What the trader can do with the chart right now. */
export interface InteractionState {
  /** A one-finger vertical drag moves the chart (true) or scrolls the page (false, the default for a chart in a page). */
  vertTouchDrag: boolean;
  /** Every scroll and zoom off for a moment (a drawing is being dragged). */
  suspended: boolean;
}

export const DEFAULT_INTERACTION: Readonly<InteractionState> = { vertTouchDrag: false, suspended: false };

export interface HandleScrollOptions { mouseWheel: boolean; pressedMouseMove: boolean; horzTouchDrag: boolean; vertTouchDrag: boolean }
export interface HandleScaleOptions {
  mouseWheel: boolean; pinch: boolean;
  axisPressedMouseMove: { time: boolean; price: boolean };
  axisDoubleClickReset: { time: boolean; price: boolean };
}
export interface InteractionOptions { handleScroll: HandleScrollOptions; handleScale: HandleScaleOptions }

/** The gesture options for a state, always as full objects. Touch as on ProChart (PW2): pinch to zoom, a one-finger
 * drag pans, a vertical swipe scrolls the page unless the state says otherwise; double-click / double-tap on an axis
 * resets that axis; dragging an axis stretches it. */
export function interactionOptions(state: Readonly<InteractionState>): InteractionOptions {
  const on = !state.suspended;
  return {
    handleScroll: { mouseWheel: on, pressedMouseMove: on, horzTouchDrag: on, vertTouchDrag: on && state.vertTouchDrag },
    handleScale: {
      mouseWheel: on, pinch: on,
      axisPressedMouseMove: { time: on, price: on },
      axisDoubleClickReset: { time: on, price: on },
    },
  };
}

/** The font of both charts' axes and labels. */
export const CHART_FONT_FAMILY = "'JetBrains Mono', ui-monospace, monospace";

/** The theme-neutral options every chart starts from: gestures, kinetic scroll (touch only), the price axis width and
 * the bars of free space right of the last bar. The callers add their theme, size, time formatters and layout. */
export function baseChartOptions(state: Readonly<InteractionState> = DEFAULT_INTERACTION) {
  return {
    ...interactionOptions(state),
    kineticScroll: { touch: true, mouse: false },
    rightPriceScale: { minimumWidth: 72 },
    timeScale: { secondsVisible: false, rightOffset: 4 },
  };
}
