import type { CoreTone } from "./aiCoreTypes";

const TONE_VAR: Record<CoreTone, [string, string]> = {
  bullish: ["--up", "--ai-2"],
  bearish: ["--down", "--ai"],
  neutral: ["--ai", "--ai-2"],
};

/** The AI Core without WebGL or with motion reduced: a still SVG orb in the same regime colours. */
export default function AICoreFallback({ tone, className }: { tone: CoreTone; className?: string }) {
  const [a, b] = TONE_VAR[tone];
  const id = `core-${tone}`;
  return (
    <div className={className} data-testid="ai-core-fallback">
      <svg viewBox="0 0 200 200" className="h-full w-full" aria-hidden="true">
        <defs>
          <radialGradient id={`${id}-fill`} cx="38%" cy="34%" r="70%">
            <stop offset="0%" stopColor={`rgb(var(${b}))`} stopOpacity="0.95" />
            <stop offset="45%" stopColor={`rgb(var(${a}))`} stopOpacity="0.75" />
            <stop offset="100%" stopColor={`rgb(var(${a}))`} stopOpacity="0.08" />
          </radialGradient>
          <radialGradient id={`${id}-halo`} cx="50%" cy="50%" r="50%">
            <stop offset="55%" stopColor={`rgb(var(${a}))`} stopOpacity="0.28" />
            <stop offset="100%" stopColor={`rgb(var(${a}))`} stopOpacity="0" />
          </radialGradient>
        </defs>
        <circle cx="100" cy="100" r="96" fill={`url(#${id}-halo)`} />
        <circle cx="100" cy="100" r="58" fill={`url(#${id}-fill)`} />
        <ellipse cx="100" cy="100" rx="82" ry="26" fill="none" stroke={`rgb(var(${b}))`} strokeOpacity="0.45" strokeWidth="1.2" transform="rotate(-18 100 100)" />
        <ellipse cx="100" cy="100" rx="74" ry="20" fill="none" stroke={`rgb(var(${a}))`} strokeOpacity="0.35" strokeWidth="1" transform="rotate(28 100 100)" />
        {Array.from({ length: 28 }, (_, i) => {
          const ang = (i / 28) * Math.PI * 2;
          const r = 66 + (i % 3) * 9;
          return <circle key={i} cx={100 + Math.cos(ang) * r} cy={100 + Math.sin(ang) * r * 0.82} r={i % 4 === 0 ? 1.8 : 1.1}
                         fill={`rgb(var(${i % 2 ? a : b}))`} opacity={0.35 + (i % 5) * 0.1} />;
        })}
      </svg>
    </div>
  );
}
