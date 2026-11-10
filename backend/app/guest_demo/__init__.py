"""M95 PR-5 (owner order M95 F3, docs/design/M95_FRONT_DOOR.md; M95-Q1 approved): "Look inside without login".

A visitor without an account sees one frozen past market day of the two index references NIFTY 50 and BANK NIFTY
(OPEN_QUESTIONS LAB-14): a Today summary, a Daily chart with its areas, the option chain's open interest by strike
distance, a screen run and the daily report - built ONCE from the shared end-of-day lake by the operator's command and
stored. The public read serves only that stored snapshot.

The public surface rule (CLAUDE.md §3, M95-Q1): index data only, as of a date at least `MIN_AGE_DAYS` old, every price
figure and price axis hidden. The chart is drawn in % from the day's close, strikes are steps from the at-the-money
strike, open interest is in lakhs; % and structure words stay. The builder refuses to store a snapshot that still holds
a number in an index's price range (`snapshot.price_figures`).

* `snapshot.py` - pure functions: the chart, the chain, the screen run, the Today and report lines, the price check;
* `build.py`    - the operator's command `python -m app.guest_demo.build --as-of <date>` (reads `md_candles` only);
* `routes.py`   - `GET /api/guest-demo` (no sign-in, per-IP limit, flag `guest_demo` read globally, 404 when off or
                  when nothing is stored).

No broker call, no AI call, no per-user data. Nothing here places, blocks or changes an order.
"""
FLAG = "guest_demo"
KEY = "guest_demo_snapshot"           # the platform_controls row that holds the stored snapshot (no migration)
MIN_AGE_DAYS = 90                     # M95-Q1 / CLAUDE.md §3: public charts are at least three months old
VERSION = 1

# The two index references (lake name -> the name a visitor reads). Nothing else is ever named.
INDICES: dict[str, str] = {"NIFTY 50": "NIFTY 50", "NIFTY BANK": "BANK NIFTY"}
SHOWN_NAMES: tuple[str, ...] = tuple(INDICES.values())
