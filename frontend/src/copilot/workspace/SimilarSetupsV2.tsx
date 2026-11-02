import { useEffect, useState } from "react";
import { cx } from "../../components/primitives/cx";
import { Details } from "../../ui/Details";
import { formatDateIST } from "../../ui/format";
import type { Annotations } from "./annotations";
import { keyWords, similarApi, similarKey, type SimilarApi, type SimilarRead } from "./similar";

/**
 * M76 MP-14 L-1 (owner order M76 L-1; flag `copilot_workspace`): his own past trades at the same kind of area as the one
 * the chart's price is inside now, with their outcomes - beside the chart, numbers first. Read-only (no advice; the
 * setup recall's own words). Rules: `similar.ts` (similar.test.tsx).
 */
const TONE = { gain: "text-up", loss: "text-down", flat: "text-fg" } as const;

export function SimilarSetupsV2({ annotations, api = similarApi }: { annotations: Annotations | null; api?: SimilarApi }) {
  const key = similarKey(annotations);
  const id = key ? `${key.source}|${key.tf}|${key.side}|${key.state ?? ""}` : null;
  const [read, setRead] = useState<SimilarRead | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    setRead(null); setFailed(false);
    if (key) api.read(key).then((r) => { if (live) setRead(r); }, () => { if (live) setFailed(true); });
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- `id` is the key's identity
  }, [id, api]);

  return (
    <div className="space-y-space-2 text-t13 text-fg" data-similar={!key ? "no-area" : read ? read.count : failed ? "failed" : "loading"}>
      <h4 className="text-t13 font-semibold text-fg">Similar past setups</h4>
      {!key && <p className="text-t12 text-fg-muted">The last close is not inside an area on this chart.</p>}
      {key && <p className="text-t12 text-fg-muted" data-similar-key>{keyWords(key)}</p>}
      {failed && <p className="text-t12 text-fg-muted">Your past trades could not be read just now.</p>}
      {read && read.sides.length === 0 && <p className="text-t12 text-fg-muted">{read.line}</p>}
      {read && read.sides.map((g) => (
        <div key={g.side} data-similar-side={g.side}>
          <p className="font-tabular"><span className="font-semibold">{g.side_words}:</span> {g.line}</p>
          <ul className="mt-space-1 divide-y divide-subtle">
            {g.matches.map((m) => (
              <li key={m.trade_id} className="flex flex-wrap justify-between gap-x-space-2 py-space-1 font-tabular" data-similar-trade={m.trade_id}>
                <span className="text-fg-muted">{m.symbol ?? ""} {m.closed_at ? formatDateIST(m.closed_at) : ""}</span>
                <span className={cx("font-medium", TONE[m.result ?? "flat"])}>{m.outcome}</span>
              </li>
            ))}
          </ul>
        </div>
      ))}
      <Details id="copilot.similar">
        <p className="text-t12 text-fg-muted">Your own closed trades whose entry was inside the same kind of area (the same reading source, the
          area&apos;s timeframe, its side and state), recorded when the trade was placed from this workspace. Trades without that record are not matched.</p>
      </Details>
    </div>
  );
}

export default SimilarSetupsV2;
