import type { ReactNode } from "react";

export function Card({ title, children, className = "" }: { title?: string; children: ReactNode; className?: string }) {
  return (
    <div className={`rounded-lg border border-border bg-panel p-4 ${className}`}>
      {title && <div className="text-xs uppercase tracking-wide text-muted mb-3">{title}</div>}
      {children}
    </div>
  );
}

export function StatTile({ label, value, tone = "default" }: { label: string; value: ReactNode; tone?: "default" | "up" | "down" }) {
  const toneClass = tone === "up" ? "text-accent" : tone === "down" ? "text-danger" : "text-slate-100";
  return (
    <div className="rounded-lg border border-border bg-panel px-4 py-3">
      <div className="text-[11px] uppercase tracking-wide text-muted">{label}</div>
      <div className={`text-lg font-semibold ${toneClass}`}>{value}</div>
    </div>
  );
}

export function DirectionBadge({ direction }: { direction: "LONG" | "SHORT" | "NO_TRADE" }) {
  const styles =
    direction === "LONG"
      ? "bg-accent/15 text-accent border-accent/40"
      : direction === "SHORT"
        ? "bg-danger/15 text-danger border-danger/40"
        : "bg-slate-700/30 text-muted border-border";
  return <span className={`inline-block rounded border px-2 py-0.5 text-xs font-semibold ${styles}`}>{direction}</span>;
}

export function GradeBadge({ grade }: { grade: string }) {
  const tone =
    grade === "A1"
      ? "bg-accent/15 text-accent border-accent/40"
      : grade === "High Quality"
        ? "bg-sky-500/15 text-sky-400 border-sky-500/40"
        : grade === "Valid"
          ? "bg-warn/15 text-warn border-warn/40"
          : "bg-slate-700/30 text-muted border-border";
  return <span className={`inline-block rounded border px-2 py-0.5 text-xs font-semibold ${tone}`}>{grade}</span>;
}

export function DemoDataBanner() {
  return (
    <div className="mb-4 rounded-md border border-warn/40 bg-warn/10 px-3 py-2 text-xs text-warn">
      Using deterministic sample OHLCV data - no live broker is connected yet (credential
      endpoints are intentionally disabled until encrypted secrets storage exists). Every
      strategy, score and chart below is computed for real by the backend against this data.
    </div>
  );
}

export function ProgressBar({ pct }: { pct: number }) {
  const clamped = Math.max(0, Math.min(100, pct));
  const color = clamped >= 80 ? "bg-accent" : clamped >= 60 ? "bg-warn" : "bg-danger";
  return (
    <div className="h-1.5 w-full rounded-full bg-panel2">
      <div className={`h-1.5 rounded-full ${color}`} style={{ width: `${clamped}%` }} />
    </div>
  );
}
