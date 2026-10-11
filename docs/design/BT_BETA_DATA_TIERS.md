# Beta data tiers - BT1 to BT3 in Phase 0 (design note)

Spec: `docs/BETA_LAUNCH_SPEC.md` §2 (market-data model) and §4a (Phase 0, lean start). Broker terms:
`docs/BROKER_DATA_TERMS.md`. Everything here sits behind the flag **`byob_data_tier`** (Admin > Feature flags, off by
default; the flag can be turned on for an allow-list of organisations for a staged rollout). With the flag off, every
market-data read behaves exactly as before.

Phase 0 rules that shape the design:
- **No order is placed by ATP through any broker API.** Nothing in BT1 adds or enables an order path; the broker
  session is used for market data only.
- **No delayed-data tier** (no Tier B): market-wide pages are EOD (Tier C), labelled "EOD".
- **Tier A = bring your own broker**: live data only through the user's own broker session, shown only to that user,
  never pooled, never cached across users.

## Status

| Item | Scope (Phase 0) | Status |
|------|-----------------|--------|
| BT1 | BYOB data tier: broker data-terms registry, entitlement guard, per-user session fan-in, data-tier badges | Merged (#167, flag `byob_data_tier` off). |
| BT1b | Guard every live read (not only the chart endpoints), keep per-user sessions out of shared keys / pooled rows, credential holder rule, staleness from the data's own time | In review (flag still off); see "BT1b" below. **The flag must not be turned on before BT1b merges.** |
| BT2 | Approval-only mode for strategies and scans: proposal -> Telegram / in-app / push -> Approve (PAPER only in Phase 0; "Trade in your broker app" link, PW5) | Not started. Builds on PW4/PW5 (proposal card, snooze, broker app links). No API order in Phase 0. |
| BT3 | PAPER account per user on the same flow + progression ladder | Not started. |

## BT1 - what is built

### 1. Data-terms registry (the config the code reads)

`backend/app/market_data/broker_data_terms.json` (read by `app/market_data/data_terms.py`) is the machine-readable copy
of the "Tier A data registry" table in `docs/BROKER_DATA_TERMS.md`; a test fails when they disagree.

| Status | Meaning | Phase 0 brokers |
|---|---|---|
| confirmed | Live data shown to the broker's own user (Tier A) | Upstox; Dhan (needs the user's own Data API subscription) |
| execution-only | Terms forbid display on third-party platforms: Tier C in ATP, live prices in the broker's app | Zerodha |
| pending | Second wave, written confirmation not yet received: treated as execution-only | Angel One, Fyers, Groww, Kotak Neo, ICICI Breeze, 5paisa, Shoonya, Alice Blue |

An unrecorded broker is pending. Per-connection symbol caps are **to confirm** for every broker (none recorded in
writing yet), so only the ATP per-session budget applies: `BYOB_SESSION_SYMBOL_CAP` (default 50, the per-session figure
of the §4 capacity model; not a broker limit). When a broker cap is recorded the lower of the two applies.

### 2. Who "the user" is - user vs tenant isolation

Broker credentials are stored per **tenant** (`broker_credentials.tenant_id`, unique per tenant + broker +
`account_label`), with `user_id` recording who linked them. A broker's market-data entitlement is personal (it belongs
to the account holder, and every call carries that holder's token), so:

- **A user's session = a credential row the user linked themselves** (`tenant_id = user.tenant_id` and
  `user_id = user.id`). `SessionRef(user_id, tenant_id, credential_id, broker, account_label)`.
- A teammate's credential in the same tenant is **never** the user's session: with the flag on, a member without their
  own broker sees Tier C ("a broker linked by a teammate is theirs"), and `/api/market-data/sources` lists only their
  own credentials. (Flag off: unchanged - any member uses the tenant's sessions, as today.)
- In the beta, one registration = one tenant = one user, so tenant and user coincide; the user rule is what the code
  enforces, so team tenants are safe too.
- Every cache entry for a session is keyed by `SessionRef.namespace` = `u<user_id>.c<credential_id>`:
  `md:byob:<ns>:tick:...` (ticks), `md:byob:<ns>:candles:...` (candles), `md:byob:<ns>:symbols` (the session's symbol
  set). The platform-wide caches (`md:tick:*`, `md:candles:*`, which the worker and flag-off paths share) are neither
  read nor written by a Tier A read.

### 3. Entitlement guard

`app/market_data/entitlement.py`: `resolve(session, user, symbol, exchange, segment, ...) -> Entitlement`
(`resolve_many` for a batch). Tier A only when all hold:
1. the user has their own credential at a **confirmed** broker,
2. its token is usable today (`token_is_usable`: VALID and not past the broker's daily expiry),
3. the symbol fits the session's budget (see 4).

Otherwise Tier C with the reason (no broker linked / execution-only / pending confirmation / no valid token today / outside
the session budget). Wired into the user-facing live reads, flag on:

| Endpoint | Used by | Flag on |
|---|---|---|
| `GET /api/market-data/ltp` | charts' forming candle, dashboard Market pulse, position chart | Own session + own tick cache; `data_tier` badge in the body; Tier C -> 409 + `X-Data-Tier: C` |
| `POST /api/market-data/candles` | charts, research pages | Own session; candles cached for the session only; symbols outside the budget come back EOD one by one (the rest still load); `data_tier` badge |
| `POST /api/market-data/option-chains` | option chain page, scanner chain filters | The underlying counts as one symbol (F&O segment) in the budget; `data_tier` badge |
| `GET /api/market-data/sources` | the data-source picker | Only the user's own credentials, with `data_status` and `tier_a` |
| `GET /api/market-data/data-tier` (new) | badges | `{enabled:false}` with the flag off; otherwise the symbol's badge (a lookup that takes no budget slot), the market-wide EOD badge and the user's sessions |

### 4. Per-user session fan-in

`app/market_data/fanin.py`.
- **Symbol set per session** (`SessionSymbols`): demands with priority **open positions > open charts > watchlist >
  screener universe**. The API registers what the user opens (one symbol = chart priority, a batch = screener
  priority); the worker registers the user's open positions every cycle. Chart / screener / watchlist demands lapse
  when not re-asserted (10 / 15 / 30 min); positions are replaced wholesale. In-process plus a Redis mirror, so the API process and
  the worker plan from the same set (without Redis each process plans from what it saw).
- **Plan** (`plan_symbols`): the `cap` symbols with the lowest priority number, then the longest held - a held position,
  chart or watchlist symbol keeps its slot and a new one waits (no flapping); in the screener universe the latest scan
  wins. **Graceful degradation**: beyond the cap symbols are EOD for that user,
  and the refresh hint slows (5 s normal, 10 s over half the cap, 15 s over the cap).
- **Worker side** (`UserSessionFanIn.sync`, every worker cycle): when the flag is on for the tenant and the market is
  open, one live session per user credential at a confirmed broker with a usable token (i.e. after the user's daily
  broker login). It reuses the Phase S `TickStream` (Upstox Market Data Feed V3) when `STREAMING_QUOTES_ENABLED` is on,
  writing into the session's own `TickCache(namespace=...)`; the stream does **not** feed platform-wide tick listeners
  (the lake recorder), so per-user data is never pooled. Without streaming the session is REST-only through the same
  user's adapter. **Day end**: market closed, token no longer usable, or a new IST day -> the session is closed and its
  cache and symbol set are cleared.

### 5. Daily-login reminder for the data session

The D5 pre-open reminder (`app/brokers/login_reminder.py`) reminds an organisation whose ACTIVE deployment's broker has
no session for the day. With the flag on, each user whose own credential is at a confirmed broker and whose session
will not last the day also gets their own reminder ("Live data: log in to Upstox before the open"), once per user per
IST day, addressed to that user. Until they log in, their pages show EOD.

### 6. Data-tier badges and staleness

Every Tier A / Tier C answer carries a badge: `tier`, `label` ("Live · your Upstox" / "EOD · <date>"), `as_of`
(exchange time of the tick or quote), `eod_date` (the newest exchange EOD file, `index_eod`), `stale`,
`stale_reason`, `alerts_suppressed`, the session `cap` and `poll_seconds`. Staleness reuses the existing rules and is
timed by the **data's own time** (BT1b): a tick / quote by `freshness.quote_is_stale` (Phase G1, `QUOTE_MAX_STALE_SECONDS`),
bars by the newest bar through the G1 candle rule (`candle_staleness`, with its daily allowance for weekends and
holidays; a batch is as fresh as its stalest symbol), EOD by "older than the last closed session" (holiday-aware). A
time the broker does not give (a bare LTP, an option chain, a symbol with no tick yet) is **unknown = stale** - it fails
closed. `alerts_suppressed = stale`: stale data must not raise alerts (spec §2, U3).

Frontend: `DataTierBadge` (tier + IST time or EOD date, toned live / EOD / stale, hover text with the reason) on the
chart window, the dashboard Market pulse charts, the position chart, the data-source bar of the research pages (option
chain, scanner, signals, backtest, quant), and on the screener next to the run's freshness - the tier the run actually
read (BT1b; the screener is not an EOD page, so the market-wide "EOD" badge is no longer shown there). `MarketWideTierBadge`
("EOD · <date>") stays available for the EOD pages to come. With the flag off the backend sends no badge and the
components render nothing.

## BT1b - every live read through the guard (flag still off)

An independent review of BT1 found live reads outside the chart endpoints that, with the flag on, would still use the
organisation's credential (any broker, a teammate's, Zerodha) and the platform-wide caches. BT1b closes them; with the
flag off each path is unchanged. One door: `app/market_data/live_access.py` (`user_source`: the signed-in user's own
confirmed session for the symbols, namespaced cache, `LiveDataRefused` = 409 + `X-Data-Tier: C` otherwise; a read with no
user behind it gets no live data).

| Path | Flag on |
|---|---|
| Screener run (`/api/screener/run`, `fetch_frames`) | The runner's own session; symbols outside the budget are reported EOD-only; bars cached per session; the response carries the run's real `data_tier`. No own session -> 409. |
| Alert rules (`alerts/barclose`) | Bars through the rule creator's own session (`user_id=rule.created_by`); the cycle cache and result cache are per user. |
| Copilot evidence (`ai/evidence.server_frame`), tools (`get_candles`, `get_quote`, `get_option_chain`, regime), strategist frames | The asking user's own session, namespaced cache; an organisation-level caller or another organisation's user -> 409. |
| Market-memory refresh, worker market memory and sentiment | Organisation-wide stores: not fed from a per-user session; global cues only. |
| Portfolio exposure, open-position Greeks, deployment preview (spot, chain, leg quotes), PAPER exit price on an approved proposal | The user's own session; without one: entry prices / 409 / no quotes / no lookup, as when no broker session existed. |
| Worker (`trading_worker`) | No org-wide `StreamManager` for the organisation (a user's credential streams only through the fan-in, into its own keys, never the lake - also no second websocket on the same token); the organisation's market data keeps its own cache keys (`md:byob:org.t<id>:...`, never `md:candles:*` / `md:tick:*`); no platform-wide option-chain recording; the OI-banner collector never reads through it; a confirmed broker is preferred as its data source; the fan-in sync runs in its own DB session. |

Broker credential holder (`app/brokers/routes.py`; applies with the flag off too): `broker_credentials.user_id` is the
account holder - the person who linked the connection and logs in to it. Another member storing over the row starts
from nothing of the holder's (no merged secrets, no stored access token; the API key is required) and becomes the
holder; another member may not log in to it (`/authenticate`, `/login-code`, Upstox OAuth start -> 403; an OAuth callback
whose state names someone else -> back to the UI with `error=not_holder`). The holder's own updates merge as before.

`/api/market-data/sources` with the flag on: `usable` = usable for data in ATP (valid token **and** confirmed terms);
`token_usable` keeps the token state.

Not changed (recorded): the worker's own deployment evaluation is organisation-level (one data session per
organisation, as before; in the beta an organisation is one user). With only an execution-only broker linked it still
evaluates on it - see open question BT1-4. Notifications (the per-user login reminder) are an organisation-wide feed
in the current model; the reminder is addressed to the user (`user_id`) but visible to teammates - BT1-5.

## Follow-ups (not in this PR)

- Per-user OI banner (today the collector is platform-wide and, with the flag on, never reads through a per-user
  session); server-side watchlist / screener-universe demands for the fan-in; the alert engine honouring
  `alerts_suppressed` from the badge directly (it already skips symbols whose bars have not reached the expected bar).
- **Tier C chart bars**: charts for Tier C users currently show the 409 reason instead of bars; serve daily bars from
  the lake / bhavcopy as EOD.
- Per-user process budget (CPU / memory) is covered only by the symbol budget and the REST fallback.
- Record each broker's per-connection caps (with the written confirmation) in `docs/BROKER_DATA_TERMS.md`.

## Open questions (also in docs/OPEN_QUESTIONS.md)

- BT1-1 Dhan without the user's Data API subscription: ATP cannot see the subscription; the user gets Dhan's refusal as an
  error. Should the user tick "I have Dhan Data API" in Settings before Dhan is offered for data?
- BT1-2 Per-connection caps for Upstox / Dhan: to confirm in writing (the ATP budget of 50 applies until then).
- BT1-3 Team tenants: a teammate never uses another member's broker data. Confirm this is the intended beta rule (it is
  the strict reading of "shown only to that user").
- BT1-4 The worker's deployment evaluation (PAPER automation) is organisation-level: with the flag on it prefers a
  confirmed broker but still evaluates on an execution-only one when that is all the organisation has. Refuse instead
  (deployments wait with "link Upstox or Dhan")? Not changed here because the same early return also stops exits.
- BT1-5 Per-user notifications: the notification feed is organisation-wide; make user-addressed ones (the data login
  reminder) visible only to that user?
