import { useState } from "react";
import { api } from "../api/client";
import type { WhyRow } from "../types";
import { Chip } from "../ui/Chip";
import { formatDateTimeIST } from "../ui/format";

/**
 * AUD-T3 (owner audit 28 Oct; flag `why_no_trade`): "Why no trade?" on a bot's row - its last closed bars, newest first,
 * each with the reason it did or did not trade in plain words and the worker's own detail under it. Closed by default;
 * loaded when opened. Information only: nothing here changes the bot.
 */
/** The codes that mean "a trade happened or was offered" - shown with a positive chip, the rest neutral. */
const TRADED = new Set(["entered", "proposed"]);

export function whyWhen(row: WhyRow): string {
  return row.bar_time ? `Bar ${formatDateTimeIST(row.bar_time)}` : `Seen ${formatDateTimeIST(row.seen_at)}`;
}

export function BotWhyV2({ botId, load = (id: number) => api.whyNoTrade(id) }: {
  botId: number; load?: (id: number) => Promise<{ rows: WhyRow[] }>;
}) {
  const [rows, setRows] = useState<WhyRow[] | null>(null);
  const [failed, setFailed] = useState(false);
  function open(next: boolean) {
    if (!next || rows) return;
    load(botId).then((d) => setRows(d.rows), () => setFailed(true));
  }
  return (
    <details data-bot-why={botId} className="group" onToggle={(e) => open(e.currentTarget.open)}>
      <summary className="inline-flex min-h-11 cursor-pointer list-none items-center rounded-control text-t13 font-medium text-fg-muted hover:text-fg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand md:min-h-0 [&::-webkit-details-marker]:hidden">
        Why no trade? Last bars
      </summary>
      <WhyList rows={rows} failed={failed} />
    </details>
  );
}

/** The list itself (rendered once loaded): newest bar first, the reason as a chip and the detail under it. */
export function WhyList({ rows, failed = false }: { rows: WhyRow[] | null; failed?: boolean }) {
  return (
    <div className="mt-space-2 text-t13" data-why-list>
      {failed ? <p className="text-fg-muted">Could not load the reasons. Try again later.</p>
        : !rows ? <p className="text-fg-muted" aria-busy="true">Loading…</p>
        : rows.length === 0 ? <p className="text-fg-muted">No bar read yet. Reasons appear once the bot has looked at a closed bar.</p>
        : (
          <ol className="space-y-space-2" aria-label="Last bars, newest first">
            {rows.map((r, i) => (
              <li key={i} className="flex flex-col gap-space-1" data-why-code={r.code}>
                <span className="flex flex-wrap items-center gap-space-2">
                  <span className="text-fg-muted">{whyWhen(r)}</span>
                  <Chip tone={TRADED.has(r.code) ? "up" : "neutral"} variant="meaning" name="why-reason">{r.label}</Chip>
                </span>
                {r.detail && <span className="break-words text-fg-muted">{r.detail}</span>}
              </li>
            ))}
          </ol>
        )}
    </div>
  );
}
