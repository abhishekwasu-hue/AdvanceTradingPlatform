import { ImageOff } from "lucide-react";
import { useEffect, useRef, useState, type RefObject } from "react";
import { Skeleton } from "../components/primitives/Feedback";
import { chipClass } from "../ui/Chip";
import { formatNumber } from "../ui/format";
import { Signed } from "../ui/Signed";
import type { JournalApi } from "./api";
import { tagText } from "./journalModel";

/**
 * M62 JN-3: small shared parts of the v2 Journal page - the signed P&L and R, the tag chips, and his own images (an
 * attachment or a server-drawn chart snapshot), read with his token as a Blob because an <img> cannot send it.
 */

export function PnlV2({ value, paise = false }: { value: number | null | undefined; paise?: boolean }) {
  if (value == null) return <span className="text-t14 text-fg-muted">Open</span>;
  return <Signed value={value} kind="money" paise={paise} arrow={false} className="text-t14 font-semibold" />;
}

export function RV2({ value }: { value: number | null | undefined }) {
  return <Signed value={value} format={(v) => `${formatNumber(v, 2)}R`} className="text-t14" />;
}

export function TagChipsV2({ tags, max = 3 }: { tags: readonly string[]; max?: number }) {
  if (!tags.length) return null;
  const shown = tags.slice(0, max);
  return (
    <span className="inline-flex flex-wrap gap-space-1" data-tags>
      {shown.map((t) => <span key={t} className={chipClass({ variant: "outline" })}>{tagText(t)}</span>)}
      {tags.length > max && <span className="text-t12 text-fg-muted">+{tags.length - max}</span>}
    </span>
  );
}

/** His own image as an object URL; `null` path or `enabled=false` reads nothing. The URL is released on unmount. */
export function useOwnImage(api: Pick<JournalApi, "image">, path: string | null, enabled = true): { url: string | null; failed: boolean } {
  const [state, setState] = useState<{ url: string | null; failed: boolean }>({ url: null, failed: false });
  useEffect(() => {
    if (!path || !enabled) return undefined;
    let alive = true;
    let made: string | null = null;
    api.image(path).then((blob) => {
      if (!alive) return;
      made = URL.createObjectURL(blob);
      setState({ url: made, failed: false });
    }).catch(() => { if (alive) setState({ url: null, failed: true }); });
    return () => { alive = false; if (made) URL.revokeObjectURL(made); };
  }, [api, path, enabled]);
  return state;
}

/** True once the element has come near the screen (no IntersectionObserver: never, so nothing is read in tests). */
function useNearScreen<T extends Element>(): [RefObject<T>, boolean] {
  const ref = useRef<T>(null);
  const [near, setNear] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el || near || typeof IntersectionObserver === "undefined") return undefined;
    const io = new IntersectionObserver((items) => { if (items.some((i) => i.isIntersecting)) setNear(true); }, { rootMargin: "200px" });
    io.observe(el);
    return () => io.disconnect();
  }, [near]);
  return [ref, near];
}

/** The entry chart as a small picture in the list (read when the row comes near the screen). */
export function ChartThumbV2({ api, path, label }: { api: Pick<JournalApi, "image">; path: string; label: string }) {
  const [ref, near] = useNearScreen<HTMLSpanElement>();
  const { url, failed } = useOwnImage(api, path, near);
  return (
    <span ref={ref} className="inline-flex h-10 w-16 shrink-0 items-center justify-center overflow-hidden rounded-control border border-subtle bg-surface-2"
      data-chart-thumb>
      {url ? <img src={url} alt={label} className="h-full w-full object-cover" />
        : failed ? <ImageOff size={14} aria-label="No chart" className="text-fg-muted" /> : null}
    </span>
  );
}

/** A full-width picture of his own (a chart snapshot or an attachment), with a muted line when there is none. */
export function OwnImageV2({ api, path, label, missing }: { api: Pick<JournalApi, "image">; path: string | null; label: string; missing: string }) {
  const { url, failed } = useOwnImage(api, path);
  if (!path || failed) return <p className="rounded-control border border-subtle bg-surface-2 px-space-3 py-space-4 text-t13 text-fg-muted">{missing}</p>;
  if (!url) return <Skeleton className="h-40" panel label={`Loading ${label}`} />;
  return <img src={url} alt={label} className="w-full rounded-control border border-subtle" />;
}

/** A multi-line field in the v2 field look (FIELD is one line high). */
export const TEXTAREA = "w-full min-w-0 rounded-control border border-border bg-surface-2 px-space-3 py-space-2 text-t14 text-fg placeholder:text-fg-muted focus:border-brand focus:outline-none focus:ring-1 focus:ring-brand";
