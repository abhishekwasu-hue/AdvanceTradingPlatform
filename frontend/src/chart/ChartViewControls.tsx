import { ChevronsRight, MoveVertical, RotateCcw } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";
import { formatCount } from "../ui/format";
import type { Readout, ViewState } from "./gestures";

/**
 * Chart order 2.2 (review PR 2): the small floating controls at the bottom right of the price pane - "Auto" (fit the
 * prices to the screen; it turns off by itself after a vertical drag), "Reset" (auto price, the default zoom, the
 * latest bar) and "Latest" (only while the latest bar is off screen). They sit over the canvas, so nothing moves when
 * "Latest" comes and goes: the row grows to the left. Neutral: the chart's own text, axis and background tokens (they
 * sit inside the chart's `data-chart-theme` root), words and icons, never colour alone. 44 px targets on a phone, 36 px
 * from md, and a tooltip on each.
 * M75 W-1 (W3): the group carries `data-label-avoid`, so band labels are placed clear of it (ChartV2 measures it).
 */
const focus = "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand";
const btn = `pointer-events-auto inline-flex min-h-11 min-w-11 items-center justify-center gap-space-1 rounded-control border px-space-2 text-t12 md:min-h-9 md:min-w-9 ${focus}`;
const tone: CSSProperties = { color: "rgb(var(--chart-text))", backgroundColor: "rgb(var(--chart-bg) / 0.92)", borderColor: "rgb(var(--chart-axis))" };
const pressed: CSSProperties = { ...tone, backgroundColor: "rgb(var(--chart-axis))", fontWeight: 600 };

function ControlButton({ label, title, onClick, active, children, name }: {
  label: string; title: string; onClick: () => void; active?: boolean; children: ReactNode; name: string;
}) {
  return (
    <button type="button" data-view-control={name} title={title} aria-pressed={active} onClick={onClick} className={btn}
      style={active ? pressed : tone}>
      {children}
      <span>{label}</span>
    </button>
  );
}

export function ChartViewControls({ view, insets, onAuto, onReset, onLatest }: {
  view: ViewState;
  insets: { right: number; bottom: number };
  onAuto: () => void;
  onReset: () => void;
  onLatest: () => void;
}) {
  return (
    <div role="group" aria-label="Chart view" data-chart-view-controls data-label-avoid
      className="pointer-events-none absolute z-10 flex flex-row-reverse items-center gap-space-1"
      style={{ right: insets.right + 8, bottom: insets.bottom + 8 }}>
      {/* Row reversed: Reset sits in the corner, then Auto; Latest is added at the far (left) end. */}
      <ControlButton name="reset" label="Reset" title="Back to the start view: prices fitted, the usual zoom, the latest bar" onClick={onReset}>
        <RotateCcw size={14} aria-hidden />
      </ControlButton>
      <ControlButton name="auto" label="Auto" title="Fit prices to the screen" active={view.autoScale} onClick={onAuto}>
        <MoveVertical size={14} aria-hidden />
      </ControlButton>
      {!view.latestVisible && (
        <ControlButton name="latest" label="Latest" title="Go to the latest bar" onClick={onLatest}>
          <ChevronsRight size={14} aria-hidden />
        </ControlButton>
      )}
    </div>
  );
}

/** Volume in the Indian way - M61 OI-1: the app's one count formatter ("61,100", "2.5 L", "3.45 Cr"). */
export function formatVolume(v: number): string {
  return formatCount(v);
}

/** The crosshair readout: the bar under the crosshair (a long press on a phone; lifting the finger ends it). */
export function ChartReadout({ readout, format }: { readout: Readout; format: (price: number) => string }) {
  const items: [string, string][] = [
    ["Open", format(readout.open)], ["High", format(readout.high)], ["Low", format(readout.low)], ["Close", format(readout.close)],
  ];
  if (readout.volume !== null) items.push(["Volume", formatVolume(readout.volume)]);
  return (
    <dl aria-label="Bar under the crosshair" data-chart-readout className="flex basis-full flex-wrap gap-x-space-3 text-t12 tabular-nums"
      style={{ color: "rgb(var(--chart-text))" }}>
      {items.map(([k, v]) => (
        <div key={k} className="flex gap-space-1">
          <dt className="opacity-80">{k}</dt>
          <dd className="font-semibold">{v}</dd>
        </div>
      ))}
    </dl>
  );
}
