# ADR 0004: Entry refusals are cheap and many; exits are never blocked

**Date:** 2026-03, extended each phase · **Status:** accepted

## Context
Most ways an algorithmic platform hurts a user are new positions opened when they should not be:
after a kill switch, on a suspended plan, when the broker's state is uncertain, during
maintenance, from a disabled member. The opposite failure, refusing to close a position, is worse.

## Decision
One function, `entry_refusals(...)`, lists every reason a new entry must not happen (kill switches
at strategy/user/global, tenant status and plan, missing algo id, broker-uncertain flag,
maintenance mode, disabled brokers, per-user trading disable, feature flag `live_trading`, scope
`trading:live`). Every entry path calls it: worker signals, TradingView webhooks, the public API,
multi-leg structures. **No exit path consults it.** Exits, square-offs, emergency exits and the
position monitor run through kill switches, maintenance, suspension and disabled brokers.

## Consequences
- Adding a new safety rule is one line in `entry_refusals` and one test.
- Operators can be aggressive with controls because they only ever stop *new* risk.
- UI wording follows: every refusal names its rule so the user knows what to change.
