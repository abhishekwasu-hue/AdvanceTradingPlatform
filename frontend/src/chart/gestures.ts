/**
 * Chart review R3 (docs/design/CHART_COLOUR_REVIEW.md): the gesture layer of the v2 chart, free of React and of the
 * DOM so every rule has a unit test.
 *
 * lightweight-charts 5.2 pans the price on a plot drag only when auto-scale is already off when the drag starts
 * (`PriceScale.startScroll` returns early while auto-scale is on), and only a drag on the price axis turns auto-scale
 * off. So, as TradingView does: a plot drag that starts with auto-scale on and moves vertically past a small threshold
 * turns auto-scale off and pans the price itself (`setVisibleRange`); time stays the library's own. Once auto-scale is
 * off, the next drags are the library's own (time and price), and the layer does nothing. The library has no event for
 * an auto-scale change, so the "Auto" state is read back from the price scale after each gesture.
 */

/** The slice of `IPriceScaleApi` the layer uses (structural, so the fake library and tests can stand in). */
export interface PriceScaleLike {
  options(): { autoScale: boolean; mode?: number };
  setAutoScale(on: boolean): void;
  getVisibleRange(): { from: number; to: number } | null;
  setVisibleRange(range: { from: number; to: number }): void;
}

/** The library's `PriceScaleMode.Percentage`: it refuses a price scroll, so the layer leaves the price alone there. */
export const PERCENTAGE_MODE = 2;
/** Pixels of vertical movement before a plot drag takes the price along: a mostly vertical drag at once, any drag (a
 * diagonal one too) past the larger distance. A sideways pan with a small wobble stays a time pan. */
export const PRICE_PAN_START_PX = 12;
export const PRICE_PAN_ANY_PX = 40;

/** The price part of one plot drag. `begin` on the press, `move` on every pointer move, `end` on the release. */
export class PriceDrag {
  private start: { x: number; y: number } | null = null;
  private snapshot: { from: number; to: number } | null = null;
  private engaged = false;

  /** `paneHeight`: the price pane's height in pixels (the range is spread over it). */
  constructor(private readonly scale: PriceScaleLike, private readonly paneHeight: () => number) {}

  /** A press on the plot. Tracks it only while auto-scale is on (otherwise the library pans the price itself). */
  begin(x: number, y: number): boolean {
    const o = this.scale.options();
    const range = o.autoScale && o.mode !== PERCENTAGE_MODE ? this.scale.getVisibleRange() : null;
    this.start = range ? { x, y } : null;
    this.snapshot = range ? { ...range } : null;
    this.engaged = false;
    return this.start !== null;
  }

  get active(): boolean { return this.start !== null; }
  /** The drag has turned auto-scale off and is moving the price. */
  get panning(): boolean { return this.engaged; }

  /** A pointer move while pressed. True when it moved the price (the first such move turned auto-scale off). */
  move(x: number, y: number): boolean {
    if (!this.start || !this.snapshot) return false;
    const dx = x - this.start.x;
    const dy = y - this.start.y;
    if (!this.engaged) {
      const vertical = Math.abs(dy) >= PRICE_PAN_START_PX && Math.abs(dy) >= Math.abs(dx) / 2;
      if (!vertical && Math.abs(dy) < PRICE_PAN_ANY_PX) return false;
      this.engaged = true;
    }
    const height = this.paneHeight();
    if (!(height > 1)) return false;
    // As the library's own price scroll: a drag down moves the prices up the axis (the bars follow the pointer).
    const shift = dy * ((this.snapshot.to - this.snapshot.from) / (height - 1));
    this.scale.setVisibleRange({ from: this.snapshot.from + shift, to: this.snapshot.to + shift });   // turns auto off
    return true;
  }

  /** The release (or a second finger, or the chart suspended). True when this drag had moved the price. */
  end(): boolean {
    const was = this.engaged;
    this.start = null;
    this.snapshot = null;
    this.engaged = false;
    return was;
  }
}

/** What the floating controls show. */
export interface ViewState {
  /** "Auto" on: the price scale fits the bars on screen. */
  autoScale: boolean;
  /** The latest bar is on screen ("Latest" hidden). */
  latestVisible: boolean;
}

export const INITIAL_VIEW: Readonly<ViewState> = { autoScale: true, latestVisible: true };

/** The latest bar is on screen when the visible logical range reaches its index (half a bar counts). No range or no
 * bars: nothing to go to, so it counts as on screen. */
export function latestVisible(range: { from: number; to: number } | null, barCount: number): boolean {
  if (!range || barCount === 0) return true;
  const last = barCount - 1;
  return range.to >= last && range.from <= last;
}

export function sameView(a: ViewState, b: ViewState): boolean {
  return a.autoScale === b.autoScale && a.latestVisible === b.latestVisible;
}

/** The slice of a wheel event Shift + wheel needs. */
export interface WheelLike { shiftKey: boolean; deltaX: number; deltaY: number; deltaMode?: number }
/** Pixels per line for a wheel that counts in lines (`deltaMode` 1, some Firefox mice). */
const LINE_PX = 16;

/** Shift + wheel pans time. Chrome, Edge and Safari turn it into a sideways wheel (`deltaX`), which the library pans
 * by itself; where the browser does not (a mouse wheel in some Firefox setups), this gives the new scroll position
 * (bars from the right edge to the latest bar) for `scrollToPosition`, or null to leave the event to the library.
 * A wheel down goes to newer bars, as the sideways wheel does. */
export function shiftWheelPosition(e: WheelLike, position: number, barSpacing: number): number | null {
  if (!e.shiftKey || e.deltaX !== 0 || e.deltaY === 0 || !(barSpacing > 0)) return null;
  const px = e.deltaMode === 1 ? e.deltaY * LINE_PX : e.deltaY;
  return position + px / barSpacing;
}

/** The crosshair readout: the bar under the crosshair, as it came (never Heikin Ashi's averaged prices). */
export interface Readout {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number | null;
}
