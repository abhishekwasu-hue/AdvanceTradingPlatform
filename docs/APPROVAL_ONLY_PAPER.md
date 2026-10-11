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
| Console Paper-execute | Unchanged. A person pressing the button is their own approval (`origin="manual"`). |
| Multi-leg structures | Refused (not proposed in Phase 0). |
| LIVE / option / future deployments | Not proposed in Phase 0. The deployment says why. |

**The gate (the backstop).** `execute_signal_for_user` and `execute_structure` call
`app/execution/approval.py::gate_refusal` on every entry. Under approval-only mode an entry is refused, as a REJECTED
order with the reason on the order trail, unless one of these holds:

- it is a manual console entry; or
- it carries the id of an **APPROVED ENTRY proposal of the same organisation and deployment**.

The pipeline's default `origin` is "automatic". A new caller that does not say what it is gets refused, not let
through. `tests/test_z3_approval_entries.py::test_every_order_entry_point_is_reviewed_for_approval` lists every caller
of the two pipelines (by AST), and a new caller fails the test until someone reviews it.

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
- **"Exit now" is always available**, with no flag:
  - `POST /api/positions/{id}/exit-now`. It uses the live price; for PAPER without a quote, the price given; else the
    last price seen. A leg closes its whole structure.
  - The **Exit now** button on the Positions page.
  - Telegram `/exit <trade id>` (PAPER positions only; LIVE positions are closed on the web).
  - The Approve button on an "Exit now?" proposal (on Telegram too, for PAPER, with the flag on).

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
