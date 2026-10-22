# My trading defaults (Settings redesign part C)

The plan of record is `docs/design/SETTINGS_REDESIGN_REVIEW.md` (finding R7); the order is
`docs/owner-orders/ATP_SESSION_SETTINGS_REDESIGN.md` Part C. Where they differ, the review wins.

## What it is

A page of the user's own starting values: `/settings/trading-defaults` (v2 only, behind `ui_v2`). The values pre-fill a
new form in the frontend; the form says "from your defaults" next to each value it took, and the user can change it
there. It is never a hidden rule: the server stores the values (`/api/me/prefs`, namespace `trading_defaults`) and never
reads them to trade. PAPER only: nothing on the page or in the pre-fill touches the mode.

| Form | Values it takes | Where |
|---|---|---|
| Paper bot, "Start on paper" | on an index: instrument kind (options / futures), structure, width, credit %, option position, premium stop, strike rule + offset, expiry rule, lots; on any symbol: exit rules, entry mode | `frontend/src/paperbot/model.ts` `paperBotRequestWithDefaults`; a "Use my defaults for this bot" switch and the list of values taken |
| Paper bot, "More ways to trade" (the full form, paper only) | every field above, as the form's own fields | `frontend/src/pages/DeploymentsPage.tsx` (only with `paperOnly`) |
| Chain order ticket | lots, product, order type (never the side) | `frontend/src/fno/ticketModel.ts` `newDraft` |

Rules (all tested in `frontend/src/settings/tradingDefaults.test.tsx` and `backend/tests/test_settings_c_trading_defaults.py`):

- Only existing fields with the API's own limits: deployment `instrument_kind`, `option_position`, `expiry_rule`,
  `strike_rule` + `strike_offset`, `premium_stop_pct`, `max_lots`, `option_strategy` (not CUSTOM), `spread_width`,
  `target_credit_pct` / `stop_credit_pct`, `entry_mode`; ExitRules `trailing_stop_pct`, `break_even_at_r`,
  `time_exit_minutes`, `time_exit_at`; the ticket's lots / product / order type.
- Not stored, never pre-filled: `mode`, broker routing, `order_style`, any LIVE field, the symbol, the strategy, the
  timeframe, and `holding` (the strategy's candles decide it, SR-C2).
- Not built (review R7): stop source, target rule, carry-forward, "next weekly on expiry day", separate lots for spreads
  and singles, "Use this template" (waits until templates are wired).
- Empty defaults: every form builder returns today's object itself, so request bodies are byte-identical.
- A value is used only where the API takes it (a structure's credit % only with a structure, a strike offset only with
  ITM / OTM, no structure or writing on a daily strategy); a value not used is not claimed.
- APPROVAL only while the `approval_only_entries` flag allows it (the API refuses it otherwise; SR-C3).
- The time exits are on the page, empty, and ATP never fills one in (SR-10, SR-C1).
- Risk limits are a link to the Risk limits page, not a copy (they are tenant rows the server already enforces).

## C3: what other platforms preset, and what maps

Research on 22 Oct. Only what maps to an existing ATP field was kept.

| Platform | What it presets | Maps to | Kept |
|---|---|---|---|
| Zerodha Kite | No saved order defaults found; users ask for the order window to remember order type and product (it opens on Limit; one report of NRML where CNC was wanted). Products CNC / MIS / NRML; order types Market, Limit, SL, SL-M. | Ticket order type, product | Yes: the ticket's order type and product |
| Dhan | "Pre-set order size": a saved preferred quantity for quick order placement. | Ticket lots, a new bot's lots (`max_lots`) | Yes |
| Share India (for comparison, another broker) | A default order setting: order type, validity, product (intraday / delivery), quantity, auto-selected on each order. | Ticket order type, product, lots | Yes (validity: no ATP field, not kept) |
| Sensibull | Expiry choices (current, next, monthly, quarterly) on the strategy builder; lot multipliers for the display of Greeks / P&L. Lots and expiry are chosen per leg; no saved defaults found. | `expiry_rule` (NEAREST / NEXT / MONTHLY) | Yes: the expiry rule. Quarterly: no ATP rule, not kept |
| Tradetron | Deploy step: strategy, broker, a position-size multiplier (1x minimum by default), paper or live; paper is the default broker. | `max_lots` | Lots only. The mode is never a default in ATP |

Not kept: order validity, quarterly expiries, a size multiplier on someone else's strategy (marketplace, off), and any
default for the mode or the broker.

Sources:
- [Kite order types and execution (Zerodha support)](https://support.zerodha.com/category/trading-and-markets/trading-faqs/articles/order-types-and-execution)
- [Kite feature request: defaults in the order window (TradingQnA)](https://tradingqna.com/t/kite-feature-request/14639?page=2)
- [Kite products CNC / MIS / NRML (Kite developer forum)](https://kite.trade/forum/discussion/comment/3407/)
- [Pre-set order size on Dhan (Dhan blog)](https://dhan.co/blog/feature-updates/pre-set-order-size-on-dhan/)
- [Default order setting (Share India FAQ)](https://www.shareindia.com/faqs/what-is-default-order-setting-where-i-can-check-this)
- [Introducing far expiry options (Sensibull blog)](https://blog.sensibull.com/2022/03/17/introducing-far-expiry-options)
- [Sensibull feature updates: lot multipliers (Sensibull blog)](https://blog.sensibull.com/author/balajeeramachandran/page/3/)
- [Tradetron app listing: strategy, broker, multiplier, paper or live (App Store)](https://apps.apple.com/us/app/tradetron/id1540269842)
- [Tradetron review: 1x multiplier, paper trading default (CoinSpot)](https://coinspot.io/en/reviews/tradetron/)
