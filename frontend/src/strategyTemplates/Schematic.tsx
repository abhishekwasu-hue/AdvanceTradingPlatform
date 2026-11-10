import type { Schematic } from "./api";

/**
 * M94 F15: the template's schematic - our own drawing, never a third-party chart. The server runs the template's default
 * settings through the engine on the synthetic walk its tests use and sends the first signal it finds: the candles
 * rescaled to 0..100 (no real market, no symbol, no date, no price), the setup bar, the trigger bar and the level where
 * the idea is wrong. Nothing here computes a signal; it only draws what the engine marked.
 */
const STEP = 10;            // px per candle
const BODY = 6;             // candle body width
const PAD_TOP = 22;         // room for the marks above the candles
const PAD_BOTTOM = 26;      // room below
const PLOT = 120;           // the candles' height

export function schematicY(v: number, scale: number): number {
  return PAD_TOP + (1 - v / (scale || 100)) * PLOT;
}

/** The marks the drawing shows, in words (the legend under it, and the picture's accessible name). */
export function schematicMarks(s: Pick<Schematic, "same_bar" | "side">): { key: string; label: string }[] {
  const dir = s.side === "long" ? "upward" : "downward";
  return [
    ...(s.same_bar ? [{ key: "setup", label: `Setup and trigger on the same bar (${dir} idea)` }]
      : [{ key: "setup", label: "Setup" }, { key: "trigger", label: `Trigger (${dir} idea)` }]),
    { key: "invalidation", label: "Where the idea is wrong" },
  ];
}

export function SchematicSvg({ s }: { s: Schematic }) {
  const n = s.candles.length;
  const width = n * STEP + STEP;
  const height = PAD_TOP + PLOT + PAD_BOTTOM;
  const x = (i: number) => STEP / 2 + i * STEP + STEP / 2;
  const y = (v: number) => schematicY(v, s.scale);
  const long = s.side === "long";
  const inv = y(s.invalidation);
  const trig = s.candles[s.trigger];
  const setup = s.candles[s.setup];
  // the setup's dot sits on the far side of the candle from the trigger's arrow when they share a bar
  const markY = (c: [number, number, number, number], below: boolean, gap: number) => (below ? y(c[2]) + gap : y(c[1]) - gap);
  const marks = schematicMarks(s);
  return (
    <figure className="m-0" data-schematic data-side={s.side}>
      <svg role="img" aria-label={`A drawing of one example: ${marks.map((m) => m.label).join(", ")}`} viewBox={`0 0 ${width} ${height}`}
        className="block h-auto w-full max-w-full" preserveAspectRatio="xMidYMid meet">
        <line x1={x(Math.min(s.setup, s.trigger)) - STEP / 2} x2={width - STEP / 2} y1={inv} y2={inv} strokeDasharray="4 3" strokeWidth={1.5}
          className="stroke-warn" data-mark="invalidation" />
        {s.candles.map((c, i) => {
          const [o, h, l, cl] = c;
          const up = cl >= o;
          const top = y(Math.max(o, cl));
          const bottom = y(Math.min(o, cl));
          return (
            <g key={i} data-candle={i} className={up ? "fill-up stroke-up" : "fill-down stroke-down"}>
              <line x1={x(i)} x2={x(i)} y1={y(h)} y2={y(l)} strokeWidth={1} />
              <rect x={x(i) - BODY / 2} y={top} width={BODY} height={Math.max(1, bottom - top)} strokeWidth={0} />
            </g>
          );
        })}
        {!s.same_bar && (
          <circle cx={x(s.setup)} cy={markY(setup, long, 7)} r={3.5} className="fill-brand" data-mark="setup" />
        )}
        <path data-mark={s.same_bar ? "setup" : "trigger"} className="fill-brand"
          d={long
            ? `M${x(s.trigger)} ${markY(trig, true, 3)} l-4 8 h8 z`
            : `M${x(s.trigger)} ${markY(trig, false, 3)} l-4 -8 h8 z`} />
      </svg>
      <figcaption className="mt-space-2">
        <ul className="flex flex-wrap gap-x-space-4 gap-y-space-1 text-t13 text-fg-muted" aria-label="What the drawing marks">
          {marks.map((m) => (
            <li key={m.key} className="flex items-center gap-space-1" data-legend={m.key}>
              <span aria-hidden className={m.key === "invalidation" ? "inline-block h-0 w-4 border-t-2 border-dashed border-warn"
                : m.key === "setup" && !s.same_bar ? "inline-block h-2 w-2 rounded-pill bg-brand" : "text-brand"}>
                {m.key === "invalidation" || (m.key === "setup" && !s.same_bar) ? "" : long ? "▲" : "▼"}
              </span>
              {m.label}
            </li>
          ))}
        </ul>
      </figcaption>
    </figure>
  );
}
