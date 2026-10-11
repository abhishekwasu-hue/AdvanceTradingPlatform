import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";

/** PW3: a push or Telegram link opens `/notifications?n=12` or `/copilot/watchtower?action=7`. Once the list holding
 * that item has rendered (`ready`), the item `#<prefix>-<id>` is scrolled into view; the returned id lets the list
 * highlight it. */
export function useDeepLinkTarget(param: string, prefix: string, ready: boolean): number | null {
  const [params] = useSearchParams();
  const raw = params.get(param);
  const id = raw && /^\d+$/.test(raw) ? Number(raw) : null;
  const [done, setDone] = useState<number | null>(null);
  useEffect(() => {
    if (id == null || !ready || done === id) return;
    const el = document.getElementById(`${prefix}-${id}`);
    if (!el) return;
    el.scrollIntoView({ block: "center" });
    setDone(id);
  }, [id, ready, prefix, done]);
  return id;
}
