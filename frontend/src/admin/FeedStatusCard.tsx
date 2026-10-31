import { Activity, Clock, Database, Layers, RefreshCw, Rows3, ShieldOff, TriangleAlert } from "lucide-react";
import { Details } from "../ui/Details";
import { formatNumber, formatTimeIST } from "../ui/format";
import { Panel, Pill } from "./AdminSectionsV2";
import { ago, type MarketFeed, type MarketFeedConnection, type MarketFeedGap } from "./consoleApi";

/**
 * M71 PL-4: the admin "Market data feed" card (Admin › System). It reads the `market_feed` field the console's
 * `/admin/console/system` already carries (backend/app/market_data/feed_health.py) - no other request, nothing written.
 * Numbers first; colours by meaning. "Market open" (the server's market-hours answer in the same reply) only colours the
 * last-tick age: a quiet feed while the market is closed is not a problem.
 */

type Tone = "ok" | "warn" | "bad" | "off";
type ValueTone = "ok" | "warn" | "bad" | "plain";

const TH = "py-space-1 pr-space-3 text-left font-medium text-fg-muted";
const TD = "py-space-1 pr-space-3 align-top";
/** The connections table is tighter (four columns must fit a 390 px phone without sideways scroll). */
const CTH = "py-space-1 pr-space-2 text-left font-medium text-fg-muted last:pr-0";
const CTD = "py-space-1 pr-space-2 align-top last:pr-0";
const VALUE: Record<ValueTone, string> = { ok: "text-up", warn: "text-warn", bad: "text-down", plain: "text-fg" };

/** Last-tick age limits (seconds) while the market is open: above WARN amber, above BAD red. */
export const TICK_WARN_SECONDS = 30;
export const TICK_BAD_SECONDS = 120;

/** Off: the switch is off, or the feed reads with a credential other than the owner's (the only one it supports). */
export function feedIsOff(f: MarketFeed): boolean {
  return !f.enabled || f.credential !== "owner" || f.state === "off";
}

export function feedState(f: MarketFeed): { tone: Tone; label: string } {
  if (feedIsOff(f)) return { tone: "off", label: "Off" };
  switch (f.state) {
    case "live": return { tone: "ok", label: "Live" };
    case "reconnecting": return { tone: "warn", label: "Reconnecting" };
    case "no_session": return { tone: "warn", label: "Waiting for the broker login" };
    case "closed": return { tone: "off", label: "Market closed" };
    case "error": return { tone: "bad", label: "Down" };
    default: return { tone: "off", label: "Starting" };
  }
}

export function connectionState(state: string): { tone: Tone; label: string } {
  switch (state) {
    case "live": return { tone: "ok", label: "Live" };
    case "connecting": return { tone: "warn", label: "Connecting" };
    case "reconnecting": return { tone: "warn", label: "Reconnecting" };
    case "renew_needed": return { tone: "bad", label: "Login expired" };
    default: return { tone: "off", label: "Closed" };
  }
}

/** The last tick's colour: only while the market is open; green fresh, amber after 30 s, red after 120 s. */
export function tickTone(age: number | null, marketOpen: boolean): ValueTone {
  if (!marketOpen || age == null) return "plain";
  if (age > TICK_BAD_SECONDS) return "bad";
  if (age > TICK_WARN_SECONDS) return "warn";
  return "ok";
}

export function tickText(age: number | null): string {
  return age == null ? "No tick yet" : `${formatNumber(age)} s`;
}

const INTERVALS: Record<string, string> = { "1m": "1 min", "5m": "5 min", "15m": "15 min", "60m": "1 hour" };
const GAP_STATUS: Record<string, { tone: Tone; label: string }> = {
  pending: { tone: "warn", label: "Waiting" }, requested: { tone: "warn", label: "Being filled" },
  filled: { tone: "ok", label: "Filled" }, failed: { tone: "bad", label: "Could not fill" },
};

/** The readable part of an instrument key ("NSE_INDEX|Nifty 50" -> "Nifty 50"). */
export const symbolName = (key: string) => key.split("|").pop() || key;

function Tile({ icon: Icon, label, value, sub, tone = "plain", id }: {
  icon: typeof Activity; label: string; value: string; sub?: string; tone?: ValueTone; id: string;
}) {
  return (
    <div data-feed-stat={id} data-tone={tone} className="min-w-0 rounded-control border border-subtle p-space-3">
      <dt className="flex items-start gap-space-2 text-t12 text-fg-muted"><Icon size={14} aria-hidden className="mt-0.5 shrink-0" /><span className="min-w-0">{label}</span></dt>
      <dd className={`mt-space-1 font-tabular text-t15 font-semibold ${VALUE[tone]}`}>{value}</dd>
      {sub && <dd className="text-t12 text-fg-muted">{sub}</dd>}
    </div>
  );
}

function Connections({ rows, marketOpen }: { rows: MarketFeedConnection[]; marketOpen: boolean }) {
  if (rows.length === 0) return <p className="mt-space-1 text-t12 text-fg-muted">No connection open.</p>;
  return (
    <div className="-mx-space-1 overflow-x-auto px-space-1">
      <table className="w-full text-t12">
        <thead><tr><th className={CTH}>Connection</th><th className={CTH}>Symbols</th><th className={CTH}>Last tick</th><th className={CTH}>Reconnects</th></tr></thead>
        <tbody>
          {rows.map((c, i) => {
            const s = connectionState(c.state);
            const tone = tickTone(c.last_tick_age_seconds, marketOpen);
            return (
              <tr key={c.id} data-feed-connection={i + 1} className="border-t border-subtle">
                <td className={CTD}>
                  <span className="mr-space-2 font-medium text-fg">{formatNumber(i + 1)}</span><Pill tone={s.tone}>{s.label}</Pill>
                </td>
                <td className={`${CTD} font-tabular text-fg`}>{formatNumber(c.symbols)}</td>
                <td className={`${CTD} whitespace-nowrap font-tabular ${VALUE[tone]}`}>{tickText(c.last_tick_age_seconds)}</td>
                <td className={`${CTD} font-tabular text-fg-muted`}>{formatNumber(c.reconnects_today)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function PendingGaps({ gaps, total }: { gaps: MarketFeedGap[]; total: number }) {
  return (
    <Details id="console.market-feed.gaps" label={`Pending gaps (${formatNumber(total)})`}>
      {total > gaps.length && <p>The latest {formatNumber(gaps.length)} are shown.</p>}
      <div className="-mx-space-1 overflow-x-auto px-space-1">
        <table className="w-full text-t12">
          <thead><tr><th className={TH}>Symbol</th><th className={TH}>Bars</th><th className={TH}>Missing</th><th className={TH}>State</th></tr></thead>
          <tbody>
            {gaps.map((g) => {
              const s = GAP_STATUS[g.status] ?? { tone: "off" as const, label: g.status };
              return (
                <tr key={`${g.symbol}|${g.interval}|${g.start}`} data-feed-gap={g.symbol} className="border-t border-subtle">
                  <td className={`${TD} font-medium text-fg`} title={g.symbol}>{symbolName(g.symbol)}</td>
                  <td className={`${TD} whitespace-nowrap text-fg-muted`}>{INTERVALS[g.interval] ?? g.interval}</td>
                  <td className={`${TD} whitespace-nowrap font-tabular text-fg`}>{formatTimeIST(g.start, { suffix: false })}–{formatTimeIST(g.end)}</td>
                  <td className={`${TD} whitespace-nowrap`}><Pill tone={s.tone}>{s.label}</Pill></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Details>
  );
}

function Advanced({ f }: { f: MarketFeed }) {
  return (
    <Details id="console.market-feed.advanced" label="Advanced">
      <ul data-feed-advanced className="space-y-space-1">
        <li>Switch: MARKET_FEED_ENABLED={f.enabled ? "true" : "false"} · MARKET_FEED_CREDENTIAL={f.credential} (server settings)</li>
        {f.broker && <li>Broker: {f.broker}</li>}
        {f.per_connection_cap != null && <li>Limit: {formatNumber(f.per_connection_cap)} symbols per connection × {formatNumber(f.max_connections ?? 1)} connections</li>}
        <li>Late ticks today: {formatNumber(f.late_ticks_today)}</li>
        {f.universe_at && <li>Symbol list refreshed {ago(f.universe_at)}</li>}
        {f.note && <li>Last note: {f.note}</li>}
      </ul>
    </Details>
  );
}

const NOTE = "The live feed builds 1-minute bars for every symbol in the list. After a reconnect only the missing minutes are "
  + "filled from the broker's history. Held back: index symbols kept out of the shared store. Counters restart each day.";

export default function FeedStatusCard({ feed, marketOpen }: { feed: MarketFeed | undefined; marketOpen: boolean }) {
  if (!feed) return null;
  const state = feedState(feed);
  const off = feedIsOff(feed);
  return (
    <Panel title="Market data feed" id="market-feed" note={NOTE}>
      <div className="flex flex-wrap items-center gap-space-2">
        <span data-feed-state={off ? "off" : feed.state}><Pill tone={state.tone}>{state.label}</Pill></span>
        {!off && feed.broker && <span className="text-t12 capitalize text-fg-muted">{feed.broker}</span>}
        {feed.updated_at && <span className="text-t12 text-fg-muted">· updated {ago(feed.updated_at)}</span>}
      </div>
      {off ? (
        <p data-feed-off className="mt-space-2 text-t14 text-fg">Live feed is off. The owner turns it on in the server settings.</p>
      ) : (
        <>
          <dl className="mt-space-3 grid grid-cols-2 gap-space-2 sm:grid-cols-3 lg:grid-cols-4">
            <Tile id="subscribed" icon={Layers} label="Symbols subscribed" value={formatNumber(feed.symbols_subscribed)} sub={`of ${formatNumber(feed.symbols_wanted)} wanted`} />
            <Tile id="over-limit" icon={TriangleAlert} label="Over the limit" value={formatNumber(feed.symbols_over_limit)} sub="not subscribed" tone={feed.symbols_over_limit > 0 ? "warn" : "plain"} />
            <Tile id="held-back" icon={ShieldOff} label="Held back" value={formatNumber(feed.symbols_held_back)} sub="not shared" />
            <Tile id="last-tick" icon={Clock} label="Last tick" value={tickText(feed.last_tick_age_seconds)} sub={!marketOpen ? "market closed" : feed.last_tick_age_seconds == null ? undefined : "ago"} tone={tickTone(feed.last_tick_age_seconds, marketOpen)} />
            <Tile id="reconnects" icon={RefreshCw} label="Reconnects today" value={formatNumber(feed.reconnects_today)} />
            <Tile id="gaps" icon={Activity} label="Gaps found today" value={formatNumber(feed.gaps_detected_today)}
              sub={`${formatNumber(feed.gaps_filled_today)} filled · ${formatNumber(feed.gaps_pending)} pending`} tone={feed.gaps_pending > 0 ? "warn" : "plain"} />
            <Tile id="bars" icon={Database} label="Bars written today" value={formatNumber(feed.bars_written_today)} />
            <Tile id="no-volume" icon={Rows3} label="Stock bars without volume" value={formatNumber(feed.stock_bars_without_volume)} tone={feed.stock_bars_without_volume > 0 ? "warn" : "plain"} />
          </dl>
          <h3 className="mt-space-4 text-t14 font-semibold text-fg">Connections</h3>
          <Connections rows={feed.connections} marketOpen={marketOpen} />
          {feed.gaps_pending > 0 && <PendingGaps gaps={feed.pending_gaps} total={feed.gaps_pending} />}
        </>
      )}
      <Advanced f={feed} />
    </Panel>
  );
}
