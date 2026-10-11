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

## Per-user PAPER account (Z3-b)

This is behind the flag `paper_account`, which is **off by default**. While it is off, `/api/paper/*` answers 404,
the card stays hidden and the Telegram commands say they are not enabled.

**Scope: per user, not per organisation.** The beta spec asks for a "full PAPER account per user" and sends proposals
to "that user's own Telegram". A team member's practice results are not the owner's.

- Every read filters on both `tenant_id` and `user_id`.
- A position belongs to the user it was entered for:
  - the person who approved the entry proposal;
  - the console user;
  - for an organisation's automatic deployment, the worker's acting user.
- The risk engine's daily budget stays organisation-wide. Strictest wins (ADR-0003).
- In the beta every sign-up is its own organisation, so user scope and organisation scope coincide.
- Isolation tests cover another member of the same organisation and another organisation
  (`tests/test_z3b_paper_account.py`).

**The account** (`GET /api/paper/account`):

- **Starting capital.** `PAPER_STARTING_CAPITAL`, default 10 lakh. The user can change it within 10,000 to 10 crore
  with `PUT /api/paper/account`.
- **Balance** = start + net realised P&L. **Equity** = balance + the marked P&L of open positions.
- **Realised P&L** comes as gross, charges and net. Charges are ATP's dated India cost model on each trade (a contract
  note replaces the estimate).
- **Counts**: wins/losses, sessions (IST days with a PAPER trade), and the largest drawdown of the closed-trade equity
  curve.
- **Open positions** and a **journal** of the last 50 closed trades.
- **Would-have records** ("not taken"): entry proposals the user rejected, or that expired unanswered. They show their
  levels; nothing was placed.
- **Proposal counts**: approved, rejected, waiting, expired.
- Nothing is stored except the starting capital and the pause. Everything else is summed from the trades, so the
  account cannot drift from them.

**New users start in PAPER.** The account is created on first use with the default capital. Deployments already
default to PAPER, and the ladder's only open stage is PAPER.

**PAPER-scope pause** (Trade `paper/pause.py`): `POST /api/paper/pause|resume`, or Telegram `/pause` and `/resume`.

- It stops the user's **new PAPER entries** (refused in the order pipeline) and their **entry proposals** (not
  raised; the deployment says why).
- It never stops an exit. A test closes a paused user's position at its stop.
- It never touches LIVE or another user.

**Progression ladder card** (`GET /api/paper/ladder`, on the Positions page; P1 §1.4, Phase 0 = PAPER only):

| Criterion | Setting | Default |
|-----------|---------|---------|
| Sessions with a PAPER trade, at least | `PAPER_LADDER_MIN_SESSIONS` | 20 |
| Closed PAPER trades, at least | `PAPER_LADDER_MIN_TRADES` | 30 |
| Largest drawdown, % of peak equity, at most | `PAPER_LADDER_MAX_DRAWDOWN_PCT` | 10 |
| Discipline score | - | not measured until P1-e |

The card shows each value against its threshold and the next stage, "LIVE (approval-only) · Not available in Phase 0".
It states facts only: no advice wording, which a test checks, and no switch.

**Telegram** (the existing bot, with the existing whitelist and approver mapping; `paper_account` flag):

- `/status`: market, kill switch, your PAPER pause, approval-only on/off, open positions and waiting proposals. Each
  line says NA on its own failure, as in the Trade bot.
- `/pause [reason]` and `/resume`: your own PAPER-scope pause.
- `/paper`: your account (balance, gross − charges = net, counts).
- `/paper <symbol> long|short SL <price> T <price>`: the manual trigger.
  - It is checked against the live price: on a long, the stop is below and the target above; on a short, the reverse.
  - It shows the reward:risk, with a warning below 3 (the Trade bot's rule; parity cases are in the tests).
  - It raises an ENTRY proposal with Approve / Reject buttons. Approve places the PAPER order (Z3-a).
  - Unlike the Trade bot, it covers the underlying only and the target is required.

## Trade repo port map (folded into Z3)

| Trade repo | ATP |
|------------|-----|
| `trade_monitor.py` / `position_stream_monitor.py`: lock-protected exit loop, SL / target / time exits, never blocked | Already in ATP as the trading worker (Redis replica lock, ADR-0010) plus `position_monitor.monitor_open_positions` (exits before entries, one bad quote never stops the sweep). Z3 adds the target-vs-protective split (`approval.is_protective`) on top, with no second loop. |
| `paper/pause.py` | `PaperAccountRecord.paused` + `paper.service.set_paused` / `paused_reason` (per user, in the database; checked in `entry_refusals` and before raising a proposal). |
| `paper/commands.py` (/status /positions /pause /resume) | Telegram `/status`, `/pause`, `/resume` (`telegram_inbound.service._paper_command`); `/positions` already existed. |
| `paper/manual.py` (/paper: `parse`, `side_errors`, `rr`, R:R warning) | `app/paper/manual.py` (`parse`, `side_errors`, `reward_risk`, `RR_WARN`) + `/paper` raising an ENTRY proposal. |
| `paper/journal.py` (charges, gross/net P&L, would-have) | `paper.service.summary` (journal rows with gross, charges, net; `not_taken`). Charges come from ATP's dated cost model already stored on each trade. |
| `paper/lots.py` (lot size from the instrument master, config fallback) | Already in ATP: F&O contracts are sized from the instrument master's lot size (`instruments.master` / `contracts`). Phase 0 approval-only proposes the underlying only. |
| `paper/watch.py` (throttled P&L updates, "near SL/target" alerts) | Not ported in Z3: the app shows live P&L, and an exit sends its own notification. Throttled Telegram updates are left for a later item (Telegram volume per user). |
