import { Chip } from "../ui/Chip";
import { Details } from "../ui/Details";
import { CardV2, LoadFailedV2, LoadingRowsV2 } from "./CardV2";
import { CHIP_TONE, factsLine, sinceLines, sortPositions, useMyDay, type MyDay, type MyDayState } from "./myDay";

/**
 * AUD-T1 (owner audit 28 Oct): "My day in one card" at the top of Today, under the market strip (flag `my_day_card`).
 * One row per open position - the symbol, one chip from the user's own stop (or "No stop set"), the expiry in sessions
 * and the nearest area - and, once, what changed since the last session. What needs the eye comes first; how the chip
 * is read, the sources and the note live in Details. No open position: no card (Open positions already says so).
 */
export function MyDayView({ state }: { state: MyDayState }) {
  if (state.kind === "off") return null;
  if (state.kind === "ok" && state.data.positions.length === 0) return null;
  return (
    <CardV2 title="My day" count={state.kind === "ok" ? state.data.positions.length : undefined} more={{ label: "My positions", to: "/positions" }}
      testId="my-day">
      {state.kind === "loading" ? <LoadingRowsV2 /> : state.kind === "error" ? <LoadFailedV2 what="your day" /> : <MyDayBody data={state.data} />}
    </CardV2>
  );
}

function MyDayBody({ data }: { data: MyDay }) {
  const since = sinceLines(data);
  const t = data.thresholds;
  return (
    <>
      <ul className="divide-y divide-border" data-my-day-positions>
        {sortPositions(data.positions).map((p) => (
          <li key={p.id} data-my-day-row={p.id} className="py-space-2 first:pt-0">
            <div className="flex min-w-0 items-center justify-between gap-space-3">
              <span className="min-w-0 truncate text-t14 font-semibold text-fg">{p.symbol}</span>
              {p.chip ? (
                <Chip tone={CHIP_TONE[p.chip]} variant="meaning" name={`my-day-${p.chip}`}>{p.chip_label}</Chip>
              ) : (
                <span className="shrink-0 text-t13 text-fg-muted" data-my-day-nochip>{p.chip_text}</span>
              )}
            </div>
            <div className="mt-px text-t13 text-fg-muted">{factsLine(p)}</div>
          </li>
        ))}
      </ul>
      {since.length > 0 && (
        <div className="mt-space-3" data-my-day-since>
          <h3 className="text-t13 font-semibold text-fg">Since the last session</h3>
          <ul className="mt-space-1 space-y-px">
            {since.map((line) => <li key={line}><div className="text-t13 text-fg-muted">{line}</div></li>)}
          </ul>
        </div>
      )}
      <Details id="today.my-day">
        <p>{data.note}</p>
        <p>{`The chip reads only your own stop: how much room is left between your entry and your stop at the last price. Watch at ${t.watch_room_left_pct}% or less, near invalidation at ${t.near_room_left_pct}% or less.`}</p>
        <p>Areas are the stored structure areas of the underlying, measured from your strike, or from your stop.</p>
        <p>The gap compares the newest stored daily open with the close before it. Events come from your events calendar.</p>
        {data.since_last_session.empty && <p>{data.since_last_session.empty}</p>}
      </Details>
    </>
  );
}

export default function MyDayCardV2() {
  return <MyDayView state={useMyDay(true)} />;
}
