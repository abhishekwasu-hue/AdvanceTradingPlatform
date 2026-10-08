import { useCopilotT } from "../i18n";

/** The day types the briefing names, placed around a dial; the pointer sits on today's. */
export const REGIME_STOPS = ["TREND_DOWN", "VOLATILE", "RANGE", "TREND_UP"] as const;
export type DialKind = (typeof REGIME_STOPS)[number] | "UNKNOWN";

export function dialAngle(kind: DialKind): number | null {
  const i = (REGIME_STOPS as readonly string[]).indexOf(kind);
  return i < 0 ? null : -67.5 + i * 45;
}

/** The market regime as a dial: four segments (trending down, volatile, range, trending up), today's lit. */
export default function RegimeDial({ kind, detail }: { kind: DialKind; detail?: string | null }) {
  const t = useCopilotT();
  const angle = dialAngle(kind);
  const label = (k: DialKind) => t(`pulse.regime.${k}`);
  return (
    <figure className="copilot-dial" role="img" aria-label={t("pulse.regime.aria", { kind: label(kind) })}>
      <svg viewBox="0 0 200 120" className="h-full w-full" aria-hidden="true">
        {REGIME_STOPS.map((k, i) => {
          const a0 = (-90 + i * 45) * (Math.PI / 180) - Math.PI / 2;
          const a1 = (-90 + (i + 1) * 45) * (Math.PI / 180) - Math.PI / 2;
          const r = 80, cx = 100, cy = 105;
          const p = (a: number, rr: number) => `${cx + rr * Math.cos(a)} ${cy + rr * Math.sin(a)}`;
          const on = k === kind;
          const colour = k === "TREND_UP" ? "--up" : k === "TREND_DOWN" ? "--down" : k === "VOLATILE" ? "--warn" : "--ai";
          return (
            <path key={k} d={`M ${p(a0 + 0.03, r)} A ${r} ${r} 0 0 1 ${p(a1 - 0.03, r)} L ${p(a1 - 0.03, r - 18)} A ${r - 18} ${r - 18} 0 0 0 ${p(a0 + 0.03, r - 18)} Z`}
                  fill={`rgb(var(${colour}))`} fillOpacity={on ? 0.85 : 0.14} stroke={on ? `rgb(var(${colour}))` : "none"} />
          );
        })}
        {angle != null && (
          <g style={{ transform: `rotate(${angle}deg)`, transformOrigin: "100px 105px" }} className="copilot-dial-pointer">
            <line x1="100" y1="105" x2="100" y2="40" stroke="rgb(var(--fg))" strokeWidth="3" strokeLinecap="round" />
          </g>
        )}
        <circle cx="100" cy="105" r="6" fill="rgb(var(--fg))" />
      </svg>
      <figcaption className="-mt-2 text-center">
        <div className="text-sm font-semibold text-fg">{label(kind)}</div>
        {detail && <div className="text-xs text-fg-muted">{detail}</div>}
      </figcaption>
    </figure>
  );
}
