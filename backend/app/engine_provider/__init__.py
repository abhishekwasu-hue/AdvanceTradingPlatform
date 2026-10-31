"""M66 EN-1: the generic boundary to the signal engine in the private package (docs/design/PRIVATE_PACKAGE.md).

ATP is a public repository. The engine itself - every rule, setting value and name of the method it follows - lives in
the private package (`atp_private.trade_engine`), installed on the host only. This package holds just the boundary:

- `contract`: the typed interface - what ATP hands in (closed bars per timeframe, the futures volume beside an index's
  bars, the optional closing-auction / extended-session marks, the market calendar) and what comes back (areas with a
  state, swings, the higher-timeframe side, signals with a model id, an entry zone, an internal invalidation reference
  and an evidence list, and the card's fields as token ids). Neutral names only; no method content.
- `loader`: imports `atp_private.trade_engine` once per process and calls its `get_provider()`. Absent or broken =
  the feature is unavailable, with a plain reason for the admin and `deploy/atp.sh health`; ATP never crashes on it.

Nothing here places, blocks or changes an order (ADR-0004 / ADR-0006); PAPER only in Phase 0. Behind the
`trade_engine` flag (OFF by default; admin / owner only).
"""
