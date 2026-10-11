# Approval-only entries and the PAPER account (Z3 = BT2/BT3, Phase 0)

Beta spec §1 and §4a (Phase 0): every entry signal becomes a proposal sent to the user's own Telegram, in-app list and
push, with Approve / Reject / Snooze. An order is placed only after Approve, and in Phase 0 that order is PAPER. ATP
places no order through any broker API. Everything here is behind the flag `approval_only_entries`, which is **off by
default**. With the flag off, nothing changes.

## Approval-only entries (Z3-a)

| Path | With the flag on (or a deployment with `entry_mode` = APPROVAL) |
|------|------------------------------------------------------------------|
| Trading worker, deployment signal | Raises one ENTRY proposal per signal bar (`ai_actions`, action ENTRY, rule ENTRY_SIGNAL, idempotency key `deployment:<id>:<bar>`). The deployment shows "Waiting for approval: entry proposal #N". |
| TradingView webhook | Raises an ENTRY proposal (key `tv:<alert id>`). A redelivered alert is answered "already proposed". |
| Public API `POST /signals` | Raises an ENTRY proposal (key `api:<key id>:<idempotency key>`), answers `status: PROPOSED` and `proposal_id`. |
| Console Paper-execute | Raises an ENTRY proposal (source `console`): the console's price comes from candles the browser sent, so it is re-quoted at the live price on Approve like any other. A trader role is needed (a VIEWER gets 403, flag on or off). With the flag off the fill is unchanged but marked `trades.price_source = "client"` (left out of the PAPER account and ladder). |
| Multi-leg structures | Refused (not proposed in Phase 0). |
| LIVE / option / future deployments | Not proposed in Phase 0. The deployment says why. |

**The gate (the backstop).** `execute_signal_for_user` and `execute_structure` call
`app/execution/approval.py::gate_refusal` on every entry. Under approval-only mode an entry is refused, as a REJECTED
order with the reason on the order trail, unless one of these holds:

- it carries the id of an **APPROVED ENTRY proposal of the same organisation and deployment**.

There is no other way through: no `origin` value bypasses the gate (review M1).

**Who decides (review L8).** A proposal has an owner (`ai_actions.owner_user_id`): the deployment's creator, the
alert's or API key's owner, the console user, the holder of the position for an "Exit now?". Only the owner or the
organisation OWNER approves, rejects or snoozes it (web 403; Telegram says so). An approved entry is placed for the
owner, into their PAPER account. The monitoring agent's own proposals (no owner) are decided as before.

The pipeline's default `origin` is "automatic". A new caller that does not say what it is gets refused, not let
through. `tests/test_z3_approval_entries.py::test_every_order_entry_point_is_reviewed_for_approval` lists every caller
of the two pipelines (by AST), and a new caller fails the test until someone reviews it.

**Live price (review M5).** The re-quote, "Exit now" and Telegram `/paper` read the price through the deciding user:
with `byob_data_tier` on, only their own Tier A entitlement (`entitlement.resolve`) - never a teammate's credential or
a broker with pending terms; anything below Tier A is "no live price". With that flag off, the organisation's first
usable session, as before. The proposal's exchange is used (review L3).

**Approve → re-quote → PAPER order.** On Approve, `execute_entry` prices the entry again at the live LTP (the
organisation's broker session, as for exits):

- **Price through the stop or through target 1**: no trade is left. The proposal becomes EXPIRED and nothing is placed.
- **Price moved beyond the limit**: nothing is placed. The limit is `APPROVAL_REQUOTE_MAX_DRIFT_PCT` % of the proposal
  price (default 0.5) or `APPROVAL_REQUOTE_MAX_DRIFT_TICKS` ticks (default 0 = off), whichever is stricter. The
  approved row is closed as EXPIRED ("Not placed: price moved … Re-quoted as proposal #M"), and a fresh proposal at
  the new price goes out through the same channels, at most `APPROVAL_MAX_REQUOTES` times (default 2). A move in your
  favour counts as a move too: you decide again at the new price.
- **Otherwise**: one PAPER order goes through the ordinary pipeline (kill switches, platform controls, risk engine,
  order state machine) at the re-quoted price, with idempotency key `approval:<id>`. The row ends EXECUTED with the
  trade id.
- **No live price**: FAILED, and nothing is placed.

**Expiry.** An entry proposal expires `APPROVAL_ENTRY_TTL_MINUTES` after it was raised (default 15). The worker
expires due proposals of each organisation on every cycle (before Z3 it ran once a day). Approving an expired
proposal is refused with 409. Snooze never extends the expiry (PW5).

## Exits policy (ADR-0004)

- **Protective exits stay automatic**: stop loss, premium floor/ceiling, time exits, intraday square-off, kill switch,
  and every structure rule. They never look at a proposal or a snooze. `determine_exit_price` checks the stop first,
  so an open question never hides it. Any exit reason the policy does not know is treated as protective (it
  executes).
- **Target exits ask "Exit now?"** (Target 1 / Target 2 on an approval-only position). The question is an
  EXIT_POSITION proposal with rule `TARGET_1_REACHED` / `TARGET_2_REACHED`, asked once per level per position, and it
  expires after `APPROVAL_EXIT_TTL_MINUTES` (default 30). Approve exits at the price at that moment. If the position
  closes another way (stop, time, Exit now), the open question becomes EXPIRED ("Position closed: …").
- **Which positions ask (review L2)**: only open PAPER positions entered by an approved entry proposal. Every other
  position keeps its automatic target exit.
- **"Exit now"** exists with the flag on (review M4; with it off, exits run automatically and there is no new route):
  - `POST /api/positions/{id}/exit-now`, the **Exit now** button on Positions (PAPER rows), Telegram `/exit <trade id>`
    then `/exit <trade id> confirm` (review L4), and the Approve button on an "Exit now?" proposal.
  - **PAPER only.** LIVE is refused: "LIVE positions are closed in the broker app" - Phase 0 has no broker order path.
  - **Price (review M2):** the live price of the person's own entitlement, else the last price the monitor saw for the
    position. A typed price is never used; with neither, it is refused with the reason (the stop and time exits keep
    running). The reason says which: "Exit now (web, live price)" / "(web, last seen price)".
  - The position's holder or the organisation OWNER only (review L8); a trader role.
- **One close per position (review M3)**: `close_position` claims the close with a conditional UPDATE (`exit_time IS
  NULL`) before booking; a concurrent second close (worker sweep, "Exit now", an approved exit) books nothing and sends
  no second notification. Covered in-memory and, with `ATP_PG_RACE_URL` set, on a real Postgres with two parallel
  connections (`tests/test_z3_pg_close_race.py`).
- **Expiry (review M6)**: the worker's housekeeping expires proposals of every organisation every cycle (market open or
  not, with or without deployments). An APPROVED row not carried out within `ORPHAN_MINUTES` (10) was interrupted: an
  entry whose order went through is recorded EXECUTED, anything else FAILED. A new entry proposal first settles its
  deployment's overdue rows, so a stale row never swallows a signal.

## Settings

| Setting | Default | Meaning |
|---------|---------|---------|
| `APPROVAL_ENTRY_TTL_MINUTES` | 15 | Entry proposal lifetime |
| `APPROVAL_EXIT_TTL_MINUTES` | 30 | "Exit now?" lifetime |
| `APPROVAL_REQUOTE_MAX_DRIFT_PCT` | 0.5 | Largest move from the proposal price that still places, in % (0 = no % limit) |
| `APPROVAL_REQUOTE_MAX_DRIFT_TICKS` | 0 | Same limit in exchange ticks (0 = no tick limit). The stricter of the two wins |
| `APPROVAL_MAX_REQUOTES` | 2 | Re-proposals after a refused approval, before the proposal simply expires |

## Trade repo port map (folded into Z3)

| Trade repo | ATP |
|------------|-----|
| `trade_monitor.py` / `position_stream_monitor.py`: lock-protected exit loop, SL / target / time exits, never blocked | Already in ATP as the trading worker (Redis replica lock, ADR-0010) plus `position_monitor.monitor_open_positions` (exits before entries, one bad quote never stops the sweep). Z3 adds the target-vs-protective split (`approval.is_protective`) on top, with no second loop. |
| `paper/pause.py`, `paper/commands.py` (/status /positions /pause /resume), `paper/manual.py` (/paper), `paper/journal.py` (charges, net P&L, would-have), `paper/watch.py` (throttled updates) | Z3-b (stacked PR): the per-user PAPER account, ladder card and Telegram commands. |
