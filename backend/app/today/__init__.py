"""AUD-T1 (docs/owner-orders/ATP_ORDER_AUDIT_28OCT.md, T1): "My day in one card" on the Today page.

For each of the caller's OWN open positions (PAPER and LIVE alike): the nearest structure area of the underlying to the
held strike (or to the position's own stop), the expiry in sessions on the exchange calendar, what changed since the
previous session (the underlying's gap at the open from the shared daily history, today's events from the events
calendar, the nearest area's own lifecycle event when the zone store already holds one) and one state chip read only
from the user's own stop (thresholds: settings MY_DAY_*_ROOM_LEFT_PCT).

Modules: `my_day` (the read, pure helpers + the async builder), `routes` (`GET /api/today/my-day`). Behind `ui_v2` +
`my_day_card` (OFF by default): with either off the route answers 404. Read-only: nothing here places, changes or
blocks an order, and nothing is written. No migration.
"""
FLAG = "my_day_card"
