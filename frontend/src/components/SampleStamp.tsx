import { FlaskConical } from "lucide-react";

/** P0.9: laid over figures computed on SAMPLE candles - they are blurred underneath and are never performance. */
export default function SampleStamp({ label = "SAMPLE DATA - not real performance" }: { label?: string }) {
  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
      <span className="flex -rotate-6 items-center gap-1.5 rounded-md border-2 border-warn/80 bg-surface-1/90 px-3 py-1 text-xs font-extrabold uppercase tracking-wider text-warn shadow-lg">
        <FlaskConical size={14} />{label}
      </span>
    </div>
  );
}
