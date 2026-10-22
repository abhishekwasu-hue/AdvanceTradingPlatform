"""Master Order 4 slice 1 (docs/owner-orders/ATP_SESSION_GROWTH_MASTER.md part 1): the daily rhythm.

Four short digests built from the user's OWN data and the market data ATP already holds, never from sample data:

* `pre_market`  - before the open: the market mood (global cues, India VIX, FII / DII when a source is set), today's
                  events, the structure of NIFTY 50 and NIFTY BANK in words, setups from the user's own strategies and
                  scans, his open positions and his loss budget for the day;
* `mid_session` - one short pulse: what changed since the open (structure events, a position near its invalidation,
                  new setups);
* `closing`     - after the close: how the market moved (indices, breadth, sector leader and laggard), his P&L, whether
                  he stayed inside his own limits (and his discipline score when the journal is on), what to watch
                  next session;
* `weekly`      - the weekend: the week's market story, his week and behaviour patterns, the coming week's events.

Modules: `schedule` (the IST market-calendar windows, trading days only), `words` (the structure-word guard every line
passes), `build` (the four digests), `deliver` (Telegram, opt-in per user, default OFF), `routes` (`/api/rhythm/*`).

Behind `ui_v2` + `daily_rhythm` (OFF by default): with either off the routes answer 404 and the worker sends nothing.
Information and education only: a line describes the market or the user's own record; it never says buy / sell, never
names a target or a stop, and a setup is described in structure words with no price. No reward for trade count: the
digests praise staying inside one's own plan, never activity. No migration: the user's choices live in the `user_prefs`
namespace `daily_rhythm` and the "already sent" marks in the internal namespace `daily_rhythm_sent`.
"""
FLAG = "daily_rhythm"
KINDS = ("pre_market", "mid_session", "closing", "weekly")
