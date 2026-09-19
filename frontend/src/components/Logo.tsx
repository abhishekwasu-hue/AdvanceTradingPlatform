/** A small geometric mark (rounded tile with an ascending candlestick motif) plus wordmark -
 * reused in the sidebar header and the auth screen so the two "first things a user sees" share
 * one consistent brand identity instead of one being a plain text label.
 */
export function LogoMark({ size = 34 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 34 34" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="34" height="34" rx="9" fill="url(#logo-gradient)" />
      <rect x="8" y="17" width="3.4" height="10" rx="1.2" fill="white" fillOpacity="0.55" />
      <rect x="8" y="13.5" width="3.4" height="3" fill="white" fillOpacity="0.55" />
      <rect x="15.3" y="11" width="3.4" height="16" rx="1.2" fill="white" fillOpacity="0.78" />
      <rect x="15.3" y="8" width="3.4" height="2.4" fill="white" fillOpacity="0.78" />
      <rect x="22.6" y="6" width="3.4" height="21" rx="1.2" fill="#22c55e" />
      <rect x="22.6" y="6" width="3.4" height="2.4" fill="#22c55e" />
      <defs>
        <linearGradient id="logo-gradient" x1="0" y1="0" x2="34" y2="34" gradientUnits="userSpaceOnUse">
          <stop stopColor="#3b82f6" />
          <stop offset="1" stopColor="#1d4ed8" />
        </linearGradient>
      </defs>
    </svg>
  );
}

export default function Logo({ size = 34, showWordmark = true }: { size?: number; showWordmark?: boolean }) {
  return (
    <div className="flex items-center gap-2.5">
      <LogoMark size={size} />
      {showWordmark && (
        <div className="leading-tight">
          <div className="text-sm font-bold tracking-wide text-slate-50">Advance Trading</div>
          <div className="text-[11px] text-muted">Platform Console</div>
        </div>
      )}
    </div>
  );
}
