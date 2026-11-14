# USER_RUNNER - Route 1: the ATP runner on the user's own machine (design, for the owner's review)

Roadmap: FN6 / G1 (docs/design/FN6_SEBI_ALGO_NOTE.md:75). Owner decision 14 Nov (DECISIONS): "Option A" (other users'
strategies run on ATP's server, orders through per-user proxy IPs) is dropped; Route 1 is designed next. Docs only:
no code changes in this PR. Status: design, waiting for the owner's review and the lawyer (OPEN_QUESTIONS RUN-Q1).

## 0. Why (the rules this design follows)

| Rule (FN6 note) | What it says | Source |
|---|---|---|
| R10 | Strategies run on the broker's servers; a **tech-savvy client hosts at his own end, on his static IP** | FN6:37; [S3] I(h); [S4] 14; [S6] Q4-Q5 |
| R7 | A provider that places algo orders through a broker API must be exchange-empanelled | FN6:34 |
| R5 | One static IP maps to one client at a time | FN6:32 |
| R4 / R6 | Client-specific API key + whitelisted static IP; OAuth + 2FA, daily logout | FN6:31, 33 |

So for users other than the owner, ATP's server must not be the place where a strategy decides and an order leaves.
Route 1 moves exactly those parts to a program the user runs himself (the "runner"); ATP keeps the software, charts,
signals display, strategy building and monitoring. The owner's own account stays on ATP's server (his own server and IP:
the tech-savvy case), LIVE only on his words.

## 1. What lives where

| Must be on the user's side (runner) | Why |
|---|---|
| Strategy evaluation of every deployment that can place an order (DSL + public templates) | R10: the algo is hosted at the client's end |
| Order placement, modify, cancel, exits, protective stops | order messages originate from his machine |
| Broker credentials: API key / secret, access token, TOTP secret (if he uses a TOTP broker) | R4: his own key; ATP never holds them |
| The daily broker login (OAuth / 2FA) | R6 |
| Egress from his own static IP | R5 |
| OPS throttle, market protection, entry refusals, kill switch enforcement | R3, R11; must work with ATP unreachable |
| Local audit log of every order (append-only) | R12 evidence at the source |

| Can stay on ATP's server | Must NOT be on ATP's server (for other users' LIVE) |
|---|---|
| Charts, scans / screeners, backtests, signal display, strategy authoring (builder, AI drafts per ADR-0006), journal, dashboards, alerts, the PAPER path (no broker order) | Placing a user's LIVE order; holding his broker order credentials or access token; deciding a LIVE entry for him |
| Read-only mirrors of his runner's orders / positions / fills | Any path that forwards a LIVE order from ATP to his broker |

Today's code that must therefore be switched off for non-owner LIVE once Route 1 exists: broker credentials stored on
the server (`db/models.py:1244` BrokerAccountRecord, `db/models.py:1512` encrypted_api_key, envelope keys
`secrets_store/envelope.py:57-91`), the server-side TOTP auto-login (`brokers/auto_login.py:1-8`) and LIVE deployments
in the server worker (`workers/trading_worker.py:1483` `_process_tenant`). Owner-only LIVE keeps them.

## 2. Code reuse - the minimal runner module set

All of it is in the public repo (`backend/app`). Nothing from `atp-private` ships in the runner.

| Module | Role in the runner | Server coupling today | Evidence |
|---|---|---|---|
| `strategy_engine/declarative.py`, `base.py` | DSL -> Signal (ADR-0005: same code as backtest / paper / worker) | none (pandas + app.indicators) | declarative.py:21-28, 281, 336; ADR-0005:11-19 |
| `strategy_engine/indicator_strategies.py`, `combo_strategies.py`, `swing_strategies.py`, `mtf_strategy.py`, `registry.py` | public inbuilt templates | none | registry.py:1-7 |
| `indicators/*` | indicator math | none | declarative.py:23-27 |
| `brokers/base.py`, `models.py`, `exceptions.py`, `timestamps.py`, `circuit_breaker.py`, adapters (`upstox.py`, `zerodha.py`, `angel_one.py`, `dhan.py`, ...) | his broker, from his machine | adapters read `app.core.config`, `master_cache`, `instruments.master` (no DB) | base.py:118; upstox.py:12-38; zerodha.py:11-35 |
| `brokers/rate_budget.py` | per-account API budget | shared windows in Redis (`rate_budget.py:13`, `:343`) | needs a local in-process store |
| `brokers/totp.py`, `token_lifecycle.py` | daily login on his machine | token_lifecycle uses the DB | token_lifecycle.py:1-6 |
| `execution/router.py` | order routing, protected limit, entry policy | none (pure) | router.py:8-18, 110, 177, 256-268 |
| `execution/ops_throttle.py` | 10 OPS, exit lane never refuses | none | ops_throttle.py:1-15, 100-101 |
| `execution/tagging.py`, `order_safety.py`, `compliance/order_policy.py`, `compliance/rules.py` + `rulesets/in_sebi.json` | algo tag, market protection, rules as data | none | tagging.py:1-24; order_safety.py:25-51 |
| `execution/paper_broker.py`, `paper_charges.py`, `india_costs.py` | PAPER mode on the runner (same code path) | none | paper_broker.py:5-12 |
| `risk_engine/risk_manager.py` | sizing from his own settings | none | router.py:18 |
| `execution/signal_execution.py` (`entry_refusals`, `execute_signal_for_user`) | the one entry pipeline (kill switch, risk, order state) | SQLAlchemy session + Tenant / User / OrderRecord | signal_execution.py:6-40, 45, 126 |
| `risk_engine/by_default.py` (`entry_refusals`), `hierarchy.py`, `guardian.py` | ADR-0004 entry refusals, risk hierarchy | SQLAlchemy | by_default.py:43-50, 529 |
| `trading/position_monitor.py` | the one close path, paper or live | SQLAlchemy | position_monitor.py:1-6, 28-30 |
| `kill_switch/checks.py` | kill switch read | SQLAlchemy | checks.py:15-77 |
| `workers/trading_worker.py` | cycle: exits before entries, then entries | Postgres + Redis lock, many tenants | trading_worker.py:1-25, 539, 2186 |

Findings:
- The pure part (DSL, templates, indicators, router, OPS throttle, tagging, market protection, paper broker, broker
  adapters) can ship as it is.
- The pipeline part (`signal_execution`, `entry_refusals`, `position_monitor`, `kill_switch`) needs a database. The
  models already declare SQLite variants of their partial indexes (`db/models.py:193, 308, 625, 1606, 2002`), so the
  first build step is a spike: the same SQLAlchemy models on a local SQLite file for one tenant / one user. If it holds,
  the runner reuses the pipeline unchanged (parity scan = backtest = live, CLAUDE.md §6). If it does not, a store seam
  is added in the shared functions - never a second copy of the pipeline.
- The worker is multi-tenant and also runs data jobs (history, lake, chain, zones, daily report, charts:
  `trading_worker.py:1847-2166`). The runner gets a slim loop `app/runner/loop.py` that calls the same per-deployment
  functions (`_evaluate_deployment` :2186, exits first as at :1-25) for one user only, with an in-process lock instead
  of Redis.

Must NOT ship (public vs private):

| Excluded | Why | Evidence |
|---|---|---|
| `engine_provider/*` and any `atp_private.*` import (the Trade engine snapshot, chart reader method, KB, Method Pack) | private method code, never to users (atp-private CLAUDE.md) | engine_provider/__init__.py:1-17; trading_worker.py:687-689, 2125-2136; controls.py:226-230, 780-782 |
| `chart_reader/`, `price_action/` (reader port) | not needed to run a DSL deployment; keeps the runner small | - |
| Strategy Lab code modes (user-authored code) | research only, never trade, gVisor only | ADR-0005 amendment; ADR-0027 |
| AI, marketplace, billing, admin, data-lake jobs | server features | - |

A CI import-boundary test (step S1) proves the runner package imports none of the excluded modules.
Licence: the repo's code is proprietary (`LICENSE:1-3`); the runner image is distributed under it.

## 3. Runner form

| | (a) Docker image on a small VPS or home machine | (b) Desktop app / Windows service | (c) Android app |
|---|---|---|---|
| Static IP (R5) | a VPS comes with one static IP | home lines in India are usually dynamic; a static IP is an ISP add-on | mobile data IP changes; not static |
| Always on in market hours | yes (VPS) | only while the PC is on and awake | Android background limits stop long jobs |
| Reuses the Python backend as it is | yes (same base as `backend/Dockerfile`) | needs a bundled Python (PyInstaller) + service wrapper per OS | no Python runtime; a rewrite |
| Signed updates | image digest + signature | per-OS code signing certificates | Play Store |
| Beginner effort | paste one install line on a VPS | double-click installer | easiest, but not fit for purpose |
| Verdict | **recommended for the pilot** | later, for users with a static home IP | not suitable (keep the app as a monitor of the runner) |

Beginner install (a), behind `ui_v2`, all from ATP's "My runner" page:
1. "Get a small VPS" help card (any provider, smallest plan, Ubuntu); its IP is his static IP.
2. ATP shows a pairing code and ONE line to paste on the VPS (installs Docker if missing, pulls the signed image,
   starts it with `restart: always`).
3. He registers that IP with his broker (broker's own page, linked) and creates his own broker API app (his own key).
4. On the runner's page (step 5 below) he enters his API key / secret once; the runner encrypts and keeps them.
5. Daily: one "Log in to broker" button in ATP; the broker login completes on the runner (section 5).
6. ATP shows "Runner online, IP x registered, broker session valid" - green / red, no jargon.

## 4. Link runner <-> ATP

- Outbound only: the runner opens a WSS connection to ATP (HTTPS polling fallback). No inbound port on his machine.
- Pairing: a one-time code from ATP (short expiry), entered at install; the runner creates a device key pair
  (Ed25519), sends the public key, gets a device id. Every message is signed by the device key; ATP can revoke a device.

| ATP -> runner | runner -> ATP (read-only copies) |
|---|---|
| frozen strategy versions (DSL JSON, ADR-0005 immutable versions) and template ids | heartbeat (period = setting), runner version, broker session state, egress IP seen |
| his own settings: lots, legs, strikes, SL, targets, exits, risk limits (CLAUDE.md §3) | signals generated (structure / indicator words only) |
| commands: start / pause / stop a deployment, kill switch, square-off request | orders, fills, positions, P&L marks of his own positions, refusals with reasons |
| signed update notice | local audit rows (hash-chained, as `audit/log.py` / `verify_chain.py`) |

- Market data: the runner reads bars and LTPs from his own broker on his own machine; ATP does not stream market data
  to the runner. A parity row (last closed bar per deployment) is sent so ATP can say "your runner's data differs"
  when it disagrees with ATP's bars.
- Offline: the runner keeps managing open positions, stops and exits without ATP (exits never blocked, ADR-0004:9-15).
  New entries pause while ATP is unreachable beyond a grace period (a runner setting), the same principle as today's
  "Redis down = no new automated entries while exits continue" (`trading_worker.py:24-25`). ATP shows "Runner
  offline since hh:mm" and sends a Telegram alert after the grace period.
- Kill switch: ATP's kill reaches the runner as a command; the runner also has a local kill (its page / one CLI line)
  that works offline. Kill stops entries only; exits and square-offs always run (`ops_throttle.py:8-10, 100-101`).

## 5. Security

- Broker key / secret / token / TOTP secret only on the runner, encrypted at rest (AES-GCM, key from a passphrase or
  the OS keystore); never sent to ATP, never logged; the mirror schema has no credential fields (a test checks it).
- Daily login: on a home machine the broker redirect goes to the runner's localhost page. On a VPS the redirect goes
  to an ATP page that encrypts the one-time code in his browser with the runner's public key and relays it; the code
  is useless without his API secret, which only the runner has. (Each broker's redirect rules: to verify per broker.)
- Updates: images signed in CI (signature checked before the runner switches), pinned by digest, no auto-update of a
  running session in market hours.
- No secrets in git or in the image; device private keys stay on the device.

## 6. Compliance mapping

| Rule | Route 1 | Mark |
|---|---|---|
| R2 algo ID | below 10 OPS the broker sends the generic id 99999; the runner still tags every order (`tagging.py:1-24`) | satisfied (broker side) |
| R3 OPS | the runner's own OPS throttle per exchange, exit lane first (`ops_throttle.py:1-15`); the broker also rejects above its limit | satisfied |
| R4 / R5 | his own API key, his own static IP, one IP one client (`static_ip.py:1-8` records it and warns on sharing) | satisfied |
| R6 | OAuth / 2FA login on his machine daily; TOTP auto-login on the runner = FN6 Q6 | partial (Q6 lawyer) |
| R10 | hosted at the client's own end; but ATP supplies the runner software and the DSL | lawyer (RUN-Q1) |
| R7 / R8 | is ATP an algo provider when it supplies the runner + templates? registration of his algo? | lawyer (RUN-Q1) |
| R9 | DSL + public templates are white box (ADR-0005); private engine never in the runner | partial (FN6 Q4) |
| R11 market protection | router protected limit / entry policy (`router.py:256-268`), `order_safety.py:36` on MARKET / SL-M; the broker RMS too | satisfied |
| R12 audit | the broker keeps 5 years (its duty); the runner keeps a local append-only log and mirrors it to ATP | satisfied (broker) / partial (ATP retention, FN6 Q7) |

## 7. Build plan (one draft PR each, behind `runner_v1` flag + `ui_v2`)

| Step | Scope | Tokens (rough) | Migration |
|---|---|---|---|
| S1 | `app/runner/` package skeleton + import-boundary test (no `engine_provider`, `atp_private`, server-only modules) + SQLite spike of the entry pipeline for one user | ~120k | no |
| S2 | runner loop: exits first, DSL / template evaluation on broker bars, router + OPS + tagging + market protection, PAPER broker, local kill, local audit | ~250k | no (runner-local SQLite only) |
| S3 | ATP side: device pairing, runner registry, heartbeat, mirror tables, "runner offline" + Telegram | ~180k | **yes** (runner devices / heartbeats / mirrors) |
| S4 | link protocol: outbound WSS, signed messages, commands (kill / pause / square-off), strategy sync | ~150k | no |
| S5 | runner keystore + daily login (localhost and sealed relay), TOTP brokers; Angel One first (RUN-Q3) | ~150k | no |
| S6 | image build + signing in CI, install line, update channel | ~100k | no |
| S7 | "My runner" page (pair, status, IP, session, mirrored positions, kill) on desktop + phone | ~150k | no |
| S8 | pilot report: PAPER through the runner vs the server worker on the same deployments (parity), screenshots | ~80k | no |

Total about 1.2M tokens over 8 PRs. Server-side LIVE for non-owner users stays off throughout (Phase 0 lock,
`platform/phase0_lock.py`).

Pilot (RUN-Q2, answered 14 Nov: a separate small VPS, both PAPER and LIVE): the runner on a separate small VPS of the
owner's, on his own account only. PAPER first: his deployments mirrored side by side with the server worker for a few
sessions; check exits with ATP disconnected, the kill switch both ways, a broker login, a runner restart with an open
position. Then LIVE on his own account with his own settings (lots, SL, targets, exits from his dashboard, CLAUDE.md §3).
The LIVE switches stay his: `live_trading` on ATP and the runner's own LIVE enable each need his action; nothing turns
LIVE on by default or by a deploy. Exits are never blocked (ADR-0004). His current LIVE path via the ATP server IP is
unchanged.

First broker (RUN-Q3, answered 14 Nov): Angel One SmartAPI, his own SmartAPI app and API key (never ATP's).
- Daily login: client code + PIN + TOTP (`loginByPassword`; `brokers/angel_one.py:185-186`); the session lasts until
  midnight (docs/brokers/EASIEST_LOGIN.md section 5). The PIN and TOTP secret stay in the runner's keystore (S5).
- Static IP: from 1 Apr 2026 SmartAPI accepts place / modify / cancel only from the registered primary static IP (the
  VPS's IP; the primary IP can be changed once a week) (EASIEST_LOGIN.md section 5).
- Market / IOC orders are prohibited for algo orders; Angel One converts market orders to limits within its MPP band -
  the runner keeps the router's protected limit (`router.py:256-268`).
- Data terms: in ATP, Angel One's market-data terms are "pending" (docs/BROKER_DATA_TERMS.md:22, :56). In the runner the
  data stays on the user's own machine for his own use; that this is within Angel One's terms is NOT verified - to
  confirm with Angel One / the lawyer (with RUN-Q1) before users other than the owner.

## 8. Owner questions (max 3, each with a default)

| ID | Question | Default | Answer (owner, 14 Nov) |
|---|---|---|---|
| RUN-Q2 | Pilot machine for the runner | a separate small Linux VPS with its own static IP (closest to what users will do); PAPER only | a separate small VPS; both PAPER and LIVE (LIVE on his own account, his switches) |
| RUN-Q3 | First broker in the runner | Upstox only (the user's own Upstox app and key, never ATP's app); others after the pilot | Angel One (SmartAPI) |
| RUN-Q4 | Where a user's PAPER runs | on ATP's server as today (no broker order); the runner's PAPER mode is for the pilot and a dry run before his LIVE | as the default: on ATP's server, as today |

Lawyer (RUN-Q1, OPEN_QUESTIONS): does Route 1 fit the tech-savvy exception when ATP supplies the runner software and
the DSL; does ATP need RA / empanelment for its templates. Default: Route 1 for users, owner LIVE only on his words.

## Rules (owner decisions, CONFIRMED 14 Nov - "Default yes"; DECISIONS 2026-11-14 "Runner rules")

| # | Rule | Where it holds |
|---|---|---|
| R-1 | Kill switch: the USER's own. Only he turns it ON / OFF - on his runner (CLI `kill` / `resume`), or with his own button in ATP, which only relays HIS command (a command signed for his action, S4). ATP / the platform admin never stops, pauses or changes a user's runner; no platform-wide or admin kill reaches runners. It stops new entries only; exits are never blocked (ADR-0004). Default OFF. | S2: `runner/settings.py` `set_killed(origin=...)` refuses any origin but `user_local` / `user_signed`; tests `test_only_the_user_turns_his_kill_switch_on_or_off`, `test_no_runner_code_writes_the_kill_switch_except_its_one_setter`. S4: the link carries no admin kill; synced rows must not include admin kill switches / maintenance blocks for the runner |
| R-2 | Strategy evaluation only on the user's VPS: ATP sends only his saved rules and settings, never signals. | S2 loop evaluates locally; S4 sync sends rules only |
| R-3 | Templates are starting points the user saves as his own, with his own settings. | S4 strategy sync |
| R-4 | Pricing: subscription only - no per-trade fee, no profit share. | billing stays off in the runner (S2 meter seam) |
| R-5 | No "AI bot" wording, no return claims. | S7 page text |
| R-6 | Marketplace OFF. | boundary test (`app.marketplace` forbidden) |
| R-7 | A public compliance page (like OpenAlgo's). | later step (with RUN-Q1) |
| R-8 | Position mirror to ATP optional: default ON, the user can turn it off. | S4 |
| R-9 | Runner OPS limit 10/s, default ON. | S2: his broker session goes out behind `RateLimitedBroker` + `OpsThrottle` (`runner/feed.py` `protected`; rate from the compliance rules); `runner.json` `ops_throttle` (default true) |

## S1 results (branch `claude/runner-s1-boundary`)

Boundary (`backend/app/runner/manifest.py`, test `backend/tests/test_runner_s1_boundary.py`):
- 36 allowed entry modules (DSL + inbuilt templates, indicators, broker base + adapters Angel One / Upstox / Zerodha /
  Dhan, rate budget, TOTP, token lifecycle, router, OPS throttle, tagging, market protection, compliance rules, PAPER
  broker + charges, risk manager, by_default, hierarchy, guardian, kill switch, signal_execution, position_monitor).
  Their module-level import closure (what ships) is 137 modules.
- Forbidden: `atp_private`, `engine_provider`, `chart_reader`, `chart_analyze`, `price_action`, `ai`, `admin`,
  `marketplace`, `lab`, `workers`, `main`, `billing`, `market_lake`. The test walks the closure by AST (module-level and
  function-level imports, relative ones resolved, and any `import_module("atp_private...")` by name) and fails on any
  forbidden import that is not one of the 13 reviewed `KNOWN_EDGES` (and on a listed edge that no longer exists).
- Found and how they stand (no server code changed):

| Edge | Kind | Status |
|---|---|---|
| `order_persistence` -> `billing.service` (usage meter, `order_persistence.py:98`) | module level | tolerated; S2: a meter seam |
| `compliance/fo_limits` -> `market_lake.asof` (F&O ban list) | module level | tolerated; S2/S4: ban list synced from ATP |
| `execution/approval` -> `ai.*` (vision_audit, monitor, worker_bridge) | inside functions, approval-only mode | not on the automatic entry path; S2 decides the runner's approval mode |
| `platform/controls.engine_missing_flags` -> `engine_provider`, `chart_reader`, `chart_analyze` loaders | inside a function (admin flag list, `/api/system/features`) | never loaded by the pipeline; S6: the image leaves these packages out, so that function must then read an absent package as "unavailable" |
| `alerts/signed_webhooks` -> `chart_analyze.linkfetch` | inside a function | alerts only |

  No module-level import of the private package, its loaders, AI, admin, marketplace, Lab, the worker or the web app.
- Not followed: imports inside functions of non-forbidden modules (following them reaches 406 modules, most of the
  app); the runtime check below covers the real path, and the S2 loop test will cover the loop's path.

Spike (`backend/tests/runner_s1_spike_job.py`, run as a separate process by the test):
- `DATABASE_URL=sqlite+aiosqlite:///<file>`, `REDIS_URL` at a closed port, no Postgres. Schema from the models
  (`Base.metadata.create_all`), one organisation + one user, a DSL strategy (CLOSE crosses above SMA, the user's own ATR
  stop) -> Signal -> `execute_signal_for_user(mode=PAPER)` -> `POSITION_OPEN`, one PAPER position ->
  `monitor_open_positions` with a price past the stop -> closed, exit reason "Stop Loss".
- It ran with NO shim: the models' SQLite variants, the pipeline and the close path work unchanged on a file database;
  without Redis the billing meter's Redis total is skipped (best effort, as written). The process loads 161 app modules
  and none of the runtime-forbidden ones (private package, loaders, AI, admin, marketplace, Lab, worker, web app).
- The pipeline refused the first try with "Needs your setting: Holding / Order type" - correct (M68: ATP never
  pre-chooses them); the spike passes them as the bot's own values, as a deployment does.
- Notes for S2: the worker's Redis replica lock and the trigger engine's entry pause (M55 A18) are outside this path - the
  runner loop needs its in-process lock; the template engine (`strategy_templates`, H-templates) imports
  `screener.runtime` and `price_action` (public code), so shipping those templates needs `price_action` moved from the
  forbidden list to the allowed set (owner-visible change of section 2) - S1 ships the DSL + inbuilt templates only.

## S2 results (branch `claude/runner-s2-loop`)

- `python -m app.runner run --home <folder> [--once] [--broker angel_one]`: runner mode, the folder's SQLite file and
  no Redis are set before the pipeline is imported. Files in the folder: `runner.db`, `audit.jsonl` (hash-chained, every
  order intent and result), `runner.lock`, `LIVE` (his switch, default OFF; `live on|off|status`), `KILL` (his kill
  switch, R-1; `kill` / `resume`), optional `runner.json` (operational: cycle length, history to read).
- Loop (`runner/loop.py`), per cycle, in the worker's order: exits first - the square-off at his own time and the
  structure settlement (moved unchanged from the worker into `trading/square_off.py`; the worker calls it) and the
  position monitor; never gated by the kill switch, his LIVE switch, the ban list or the ATP link. Then entries per
  ACTIVE deployment: his broker's candles (`runner/feed.py`; Angel One adapter, or a PAPER-only fake feed) -> staleness
  check -> closed bars only -> the strategy -> one entry per signal bar -> `execute_signal_for_user` (the one pipeline).
- His broker session is wrapped as the worker wraps every session (`RateLimitedBroker`: rate budget in-process, the
  Phase 0 chokepoint on new entry orders, the OPS throttle - R-9, default ON).
- LIVE: the deployment's LIVE choice + ATP's Phase 0 lock + his local `LIVE` file + a real broker feed; the switch is
  read again right before the order. PAPER is the default.
- Lock (`runner/lock.py`): an OS file lock (one runner per folder; freed by the OS on a crash) + an asyncio lock (one
  cycle at a time; an overlapping cycle is skipped). Clean shutdown on SIGINT / SIGTERM: the running cycle finishes.
- S1 exceptions cut: the billing meter is a lazy import never reached in runner mode; the F&O ban list in runner mode
  is the list ATP sends (S4): unknown -> PAPER allowed with a note, LIVE F&O entry refused in plain words; exits never
  read it. `app.billing` and `app.market_lake` are now in `RUNTIME_FORBIDDEN` (the loop process loads neither).
- Not yet (each skipped in the deployment's own words): options / futures / multi-leg deployments (need the instrument
  list from ATP, S4), strategy templates (reader port), approval-only entries (the pipeline's gate refuses them;
  proposals answered in ATP, S4), the organisation's "one direction per symbol" setting (worker-only today; S8 parity
  report), the per-account rate budget's shared windows (Redis; local store with S5). Broker details are read from
  `<folder>/broker.json` (mode 600 enforced) until the keystore (S5).


## S5 results (branch `claude/runner-s5-keystore`, stacked on S1)

Keystore (`backend/app/runner/keystore.py`, CLI `backend/app/runner/keys.py`, tests `backend/tests/test_runner_s5_keystore.py`):
- One file `<home>/keystore.enc`: AES-256-GCM (the `cryptography` package already in requirements), the header
  (format, version, KDF) authenticated as associated data, a fresh nonce per write, atomic writes, files mode 600 and the
  folder 700; a file any group / other may read is refused. A POSIX lock serialises the CLI and the running runner.
- Key, chosen at `keys init`: `keyfile` (default) = 32 random bytes in `<home>/keystore.key` (600), so an unattended VPS
  runner (`restart: always`) logs in before the open with nobody typing; or `scrypt` = from a passphrase (n=2^15, r=8,
  p=1), asked at each start, for a machine the user starts himself. `rekey` switches / renews the key.
- Commands: `python -m app.runner.keys init | add angel_one | rotate angel_one [--field pin] | remove angel_one | list |
  rekey | login angel_one | status angel_one` (`--home`, default `~/.atp-runner`). API key, PIN and TOTP secret are asked
  without echo; `list` shows field names only. Once S2's `python -m app.runner` is merged it dispatches `keys` here, and
  S2's interim `broker.json` gives way to `broker_login.build_adapter` (today's session from the keystore).
- Never sent to ATP, never printed or logged: tests capture every log record, the status file, the log file and the CLI
  output and find no key, PIN, TOTP secret, passphrase or token, nor the broker's own error text.

Daily login (`backend/app/runner/broker_login.py`):
- The existing adapter's login (`AngelOneBroker.authenticate`: `loginByPassword` with client code + PIN + the TOTP code
  of the moment, `app/brokers/totp.py`), as ATP's server-side automatic login (`brokers/auto_login.py`) runs it.
- Window and retries as the server's rule IN-SEBI.login.daily (rule-set params: 45 minutes before the open, 1 retry,
  10 minutes), overridable in the runner's `runner.json` (`broker_login_minutes_before_open`, `broker_login_retries`,
  `broker_login_retry_minutes`; operational settings, not trading rules); retries back off by doubling. Trading days:
  weekends skipped; NSE holidays once S4 syncs the list (until then a holiday login is harmless).
- `run_due()` is meant for every loop cycle (S2 / S4 wire it): idempotent while today's session is stored. Failure:
  one line in `<home>/broker_login.log` and `<home>/broker_status.json`; `status_line()` is the heartbeat's line
  ("broker session valid" / "login failed: <plain reason>"). A failed login never blocks exits (ADR-0004).
- Egress: the adapter's client is built with `trust_env=False` (no proxy variable can reroute it); the adapter knows
  only Angel One's hosts (`apiconnect.angelone.in`, the scrip master on `margincalculator.angelbroking.com`; a test
  checks it), so login, orders and market data leave from this machine's IP. Nothing goes via ATP.

To verify in the pilot (a real Angel One account on the pilot VPS):
- the login with client code + PIN + TOTP from the VPS, and that the session lasts until midnight as documented;
- that orders are accepted from the registered static IP (and refused from another), and whether SmartAPI checks the
  `X-ClientPublicIP` header (the keystore's optional `public_ip`) against the registered IP;
- Angel One's error codes for a wrong PIN / TOTP: the adapter's `AUTH_ERROR_CODES` map them to "refused the login";
  a refusal with another code reads "did not answer as expected" until listed;
- whether repeated wrong logins lock the account (the default is 2 attempts a day; keep retries low);
- the TOTP code at a 30-second boundary (one retry covers it);
- no redirect step: Angel One's TOTP login has none, so the localhost / sealed-relay redirect of section 5 is for the
  OAuth brokers (Upstox, Zerodha) in a later step.

## S4 results (branch `claude/runner-s4-link`: S2 + S5 + S3 merged, then the link)

Runner side (`backend/app/runner/link.py`, `sync.py`, `broker_session.py`; tests `backend/tests/test_runner_s4_link.py`):
- Outbound HTTPS only (the design's polling fallback; WSS later). `python -m app.runner pair <code> --atp https://...
  [--name]`: the Ed25519 device key is made on the machine (`device.key`, 600), the code + public key + proof go to
  S3's pair endpoint; `link.json` (600) keeps the device id, the user it is paired to and ATP's command key (pinned).
- One signing module for both sides: `app/runner_link/signing.py` (request canonical bytes, pairing proof, command
  bytes); the runner imports only that file of app.runner_link (manifest). Timestamps strictly newer per request.
- Each cycle: broker session (S5 `DailyLogin.run_due` + `build_adapter` from the keystore; broker.json only when no
  keystore exists) -> exits -> link tick -> entries. Heartbeat every `heartbeat_seconds` (60): run state, version,
  kill switch, `status_line`, open positions. Its answer: his mirror choice (R-8), his commands, his rules' etag.
- Rules sync (R-2 / R-3): on a new etag the runner pulls `GET /api/runner/device/rules` (his strategies, his ACTIVE /
  PAUSED deployments' rule columns, his trading-settings prefs, his organisation's risk limits; never a signal, an
  evaluation, another member's row or a platform control) into its store with ATP's ids; a deployment gone from ATP
  is STOPPED locally (never deleted). `GET /api/runner/device/reference` once a day: the F&O ban list (into
  `runtime_mode.set_ban_list`) and his symbols' contracts. Until a list arrives the S2 behaviour stays.
- Kill relay (R-1): `POST /api/runner/devices/{id}/kill-switch {"on": bool}` - the logged-in owner of that device only
  (another member, another organisation, an admin: 404; a removed runner: 409); the device list carries
  `kill_relay: true` for his live devices. ATP signs the command with its command key (HKDF from the server secret,
  never stored); the runner applies it only for this device, this user, origin `user_signed`, a newer id and a valid
  signature, through S2's one setter (`set_killed(origin="user_signed")`), then acks it. Entries only.
- Offline: exits never wait; new entries pause when ATP has not answered for `link_grace_minutes` (default 3, the
  same as ATP's own offline time) - a plain line in `link.log` + the audit log; they resume when it answers.
- Mirror: orders, positions, audit summaries in S3's schema, paged (200), only while his mirror choice is ON.
- Migration: yes - `d2f4b6c8e0a1` (table `runner_commands`, column `runner_devices.broker_login`).
- Not yet: derivative deployments on the runner still skip in plain words (the contracts are stored; the contract
  resolver on the runner comes with the S8 parity work); start / pause / square-off commands (only the kill switch).

## S4b results (branch `claude/runner-s4b-fno`, stacked on S4)

Options / futures single-leg and multi-leg deployments now run on the runner (`backend/app/runner/loop.py`,
`derivatives.py`; tests `backend/tests/test_runner_s4b_fno.py` + the loop job `runner_s4b_fno_job.py`). Nothing new decides
a trade - the server's own code does, on the runner's local store:
- Contract / structure: `instruments.contracts.resolve_contract` / `instruments.spreads.resolve_structure` from the
  deployment's OWN settings (expiry rule, strike rule and offset, side, width, custom legs, strike filters against his
  broker's chain), against the contracts ATP sent (S4 `apply_reference`, ATP's instrument master spelling), off the latest
  CLOSED underlying bar. A rule that cannot be met is recorded on the deployment ("Contract not resolved: ..." /
  "Structure not built: ..."), never traded around.
- Execution: `execute_signal_for_user(contract=..., rules=...)` (lots = his own `max_lots`, premium levels, the F&O ban
  list for the entry) and `multileg.execute_structure` (one position, hedge legs before sold legs on the smart-execution
  engine and on the LIVE path, the incomplete-structure state). Holding: the worker's `_bot_holding` moved unchanged to
  `execution/order_defaults.bot_holding` (the worker calls it), so both read the same holding rule.
- Exits (never gated - ADR-0004): the square-off at his own time and the position monitor as before; added as in the
  worker: `incomplete_structures.run_exits` and the PAPER expiry settlement (`paper/expiry_settlement.settle_expired`,
  every 10 minutes, whatever the session). The settlement's close source is now a parameter (`close_of`; the server's
  call is unchanged): on the runner the expiry day's close comes from his own broker's Daily bar of that day, only once
  that bar has closed (no look-ahead); until then the position waits, as on the server.
- Angel One instrument tokens: ATP's master names a contract in Upstox's spelling; Angel One orders and quotes by its own
  symboltoken. `ContractBinder` finds the same contract in Angel One's scrip master (`get_instruments("NFO")`) by
  underlying + expiry + strike + right, refuses it when the lot sizes differ or it is missing / listed twice, and registers
  ATP's name as an alias on the adapter (`AngelOneBroker.alias_symbol`, new). Trades keep ATP's name (mirror, settlement
  and journal read it). Open positions are bound again before each exit pass (the daily login builds a new adapter).
- LIVE: still the deployment's own LIVE choice + ATP's Phase 0 lock + his local `LIVE` file + a real broker feed, plus
  ATP's LIVE state for his organisation (owner organisation only). Checked: neither the heartbeat answer nor the rules
  sync carries that state today, so every LIVE entry on the runner is refused with `LIVE_NO_ATP_STATE` ("LIVE is not
  open for your organisation on this runner ... nothing was placed; exits continue"). A server-side field is needed first.
- Boundary: 8 modules added (contracts, spreads, multileg, incomplete_structures, expiry_settlement, fno_contracts,
  instruments.master, runner.derivatives); no new forbidden edge; the loop job loads no runtime-forbidden module.

Test (fake NFO feed, PAPER): index CE single leg entered on his 1 lot, closed on his own 30% premium stop; bull put spread
(two legs) with the bought 24400 PE placed before the sold 24500 PE, both squared off at his 15:15; a banned stock: new
entry refused ("in the F&O ban period - new F&O entries refused; exits allowed"), the open position's stop exit ran; a
SWING CE past expiry settled at 24700 - strike (the expiry day's Daily close; the forming next-day bar 25000 not read),
exit time 15:30 IST of the expiry day; an index future entered and squared off.

Open points (not decided here):
- Feature flags are not synced to the runner: the local store reads every flag at its default (smart execution OFF ->
  the legacy multi-leg path; PAPER fills at the quoted premiums; LIVE still places the long wings first), and the
  F&O ban check follows the runner process's own `FO_BAN_CHECK_ENABLED` (default off, as the server's config).
- "Complete the rest" of an incomplete structure is answered in ATP; the runner gets no such command yet (only the kill
  switch), so a runner's incomplete group follows his own "if I do not answer" rule.
- The trigger engine's armed exits (`execution/trigger_engine`) are not on the runner's exit pass yet.
- Server finding: the worker sends an Upstox-spelled contract name to a non-Upstox adapter (`contract_ltp` / the leg
  orders use `tradingsymbol`); Angel One's adapter does not know that name. The runner binds it (above); the server path
  is unchanged.

## S4c results (branch `claude/runner-s4c-state`, stacked on S4b)

ATP -> runner state (`app/runner_link/service.py` `runner_state`, `app/runner/link.py`; tests
`backend/tests/test_runner_s4c_state.py`):
- The heartbeat answer carries `state`, signed with ATP's command key under its own scheme (`signing.state_message`, so a
  state is never read as a command): `live_open` for HIS organisation by the one rule every ATP path uses
  (`live_switch.live_allowed`) and its plain reason when closed (`live_switch.live_refusal`), his organisation's answer for
  each flag in `RUNNER_FLAGS`, the server's `FO_BAN_CHECK_ENABLED`, his plan's `runner` field (None: no plan has one today;
  False would pause new entries, exits continue), device / user / organisation and `issued_at` (strictly newer per runner).
- B1 (PR #1077, `claude/live-owner-only`) is NOT merged into this stack: `live_allowed` is today's LIVE-1 rule (the one
  switch). Once B1 merges, the same call returns B1's answer (owner's organisation only) with no change here. Merge note:
  B1 and S4c both edit `platform/phase0_lock.py` (`is_on` / `locked`); keep S4c's runner-mode lines first in both.
- Runner: verifies the state (ATP's key pinned at pairing, this device, this user, newer); keeps it in memory with its
  receive time (a restart starts with LIVE closed until ATP answers); writes the flags into the local store's flag row (the
  row `controls.feature_flags` reads), so the pipeline reads ATP's answers; sets `config.FO_BAN_CHECK_ENABLED` from ATP.
- LIVE on the runner (replaces `LIVE_NO_ATP_STATE`): a LIVE entry needs ATP's `live_open` for his organisation received
  within his grace setting (`link_grace_minutes`, default 3) AND his local `LIVE` file AND the deployment's own LIVE choice
  AND his real broker session; otherwise the deployment says why ("LIVE needs ATP's answer ...", "LIVE is not open for your
  organisation in ATP: <ATP's reason> ...", or S2's own reasons). In runner mode the PAPER-only lock
  (`phase0_lock.is_on` / `locked`, read by `entry_blocks`, `live_allowed_for` and the broker chokepoint) follows the same
  fresh state (`runtime_mode.live_open`), never the local store; the server's behaviour is unchanged. Exits never read it.
- Flags sent (`RUNNER_FLAGS`) - every flag the runner loop's code asks for, recorded by `tests/runner_flag_spy.py` while
  the S4b loop job runs (the test fails on a new unlisted one): `smart_execution`, `approval_only_entries`,
  `risk_by_default`, `risk_options`, `vix_gate`, `paper_account`, `experience_levels`, `discipline_engine`. Not sent:
  `phase0_paper_only` (follows `live_open`), `alerts_reliable_outbox` (ATP's alert delivery). F&O ban check: a server
  setting (env), sent as `fo_ban_check`. No admin kill switch or maintenance block is sent (R-1).
- Trigger engine (checked): resting / armed exits, combined exits (CX / SX) and trigger plans are created only by his
  manual actions on ATP's own positions (order ticket, basket, position actions, trigger plans' fired entries -
  `resting_book.arm_exit`, `group_exits.create`, `strategy_exits.arm`); no deployment entry arms one. A runner position
  lives in the runner's store, so ATP cannot arm one on it: nothing for the runner's exit pass today.
- "Complete the rest" as a runner command: not simple - it needs the incomplete group's state in the mirror, a signed
  command kind with the group id and a button on My runner; listed for a later step. Until then a runner's incomplete
  group follows his own "if I do not answer" rule (unchanged).
- Migration: no.

## S6 results (branch `claude/runner-s6-image`)

- Build context (`deploy/runner/build_context.py --out <dir>`), generated from `app/runner/manifest.py`: `app.runner.*`
  and `RUNNER_MODULES` with every import they make (module level and inside functions, any depth) EXCEPT
  `FORBIDDEN_PREFIXES`, which are never followed and never copied (451 modules, 551 files with the packages' data
  files). The private package, its loaders, AI, admin, marketplace, Lab, worker, web app, billing and the lake are
  physically absent; a path that reaches one gets an ImportError.
- S1 open item closed: `platform/controls.engine_missing_flags` and `platform/flag_state` read an absent loader
  package as "unavailable" (no crash).
- Image (`deploy/runner/Dockerfile`): `python:3.13-slim` as the backend; installs `deploy/runner/requirements.lock`
  (39 of the backend lock's packages, same pins and hashes; no uvicorn, asyncpg, AI SDKs, image / Parquet / S3,
  tracing exporters, test tools); re-checks at build that no forbidden package is present; user `runner` (uid 10001);
  volume `/runner`; healthcheck `python -m app.runner health` (reads the pid in `runner.lock`, never takes the lock);
  no `EXPOSE`.
- CI: the container-scan job builds the image from the generated context, runs one PAPER cycle and checks the
  forbidden paths; Trivy report only. shellcheck covers `deploy/runner/install.sh`.
- Release (`.github/workflows/runner-release.yml`, `runner-v*` tags only): build, smoke, push to
  `ghcr.io/<owner>/atp-runner`, cosign keyless sign by digest, self-verify, publish `install.sh` with the digest
  written in (+ its sha256) as the release asset.
- Installer (`deploy/runner/install.sh`): Docker (Ubuntu `docker.io`) if missing; cosign (pinned, checksum) verifies
  the image against this workflow's identity at a `runner-v*` tag BEFORE the pull by digest; folder 0700 owned by the
  runner uid; pairing / keystore asked only when the image has those commands (S3-S5 not merged: skipped and said
  so; until S5 `broker.json`, mode 600); `--restart unless-stopped`, read-only root, no capabilities, no port; prints
  the public IP to register at Angel One. `install | update | status | uninstall`, `--dry-run`.
- Owner one-time setup before the first release: after the first `runner-v*` tag, set the GHCR package
  `atp-runner` to Public (so a VPS pulls without a login).
- Docs: `docs/RUNNER_INSTALL.md` (beginner steps), `docs/runbooks/RUNNER_SETUP_SERVICE.md` (paid set-up checklist).

## S8 results (branch `claude/runner-s8-parity`: S4c + S6 + S7 merged, then the parity harness)

- PAPER parity, server worker vs runner loop, five owner-style cases: ALL MATCH (trades, orders, hedge-first sequence,
  P&L, reasons, settlement time). Table and differences: `docs/design/RUNNER_PARITY.md`; CI test
  `backend/tests/test_runner_s8_parity.py` fails on any mismatch.
- Fixed on the way: the organisation's "one direction per symbol" check (worker-only) moved unchanged to
  `execution/order_defaults.opposite_position`, called by the worker and the runner loop; a deployment with OI gates is
  skipped on the runner fail-closed ("Your OI gates need ATP's option-chain data ..."). Listed: RUN-S8-1 (index future
  volume), RUN-S8-2 (VIX readings) in docs/OPEN_QUESTIONS.md.
- The whole runner is one stack: S1-S5, S4b, S4c, S6, S7, S8. Flag `user_runner`: state `ready`
  (`platform/flag_state.py`), still OFF by default (`DEFAULT_OFF_FLAGS`) - the owner turns it on.
- Migration: yes for the stack (S4 `d2f4b6c8e0a1`, and S3's); S4c / S8 add none.

## Pilot runbook (owner)

1. Rent a KVM VPS: x86-64, Ubuntu LTS, a static public IP (any provider; 1 vCPU / 1 GB is enough for a few deployments).
2. Run the one-line installer from `docs/RUNNER_INSTALL.md` (S6: pulls the signed image, checks its signature, starts it
   with `restart: always`); `install.sh status` shows the runner and the VPS public IP.
3. ATP -> My runner (flag `user_runner` ON for his organisation, `ui_v2`): "Pair a runner" -> one-time code; on the VPS
   `python -m app.runner pair <code> --atp https://aialgodesk.tech --name "Pilot VPS"`; My runner shows it ONLINE.
4. `python -m app.runner keys init` then `keys add angel_one` (client code, PIN, TOTP secret, API key - asked without
   echo, never sent to ATP); `keys login angel_one` once to check.
5. Angel One SmartAPI: register the VPS public IP (from step 2) as the app's static IP.
6. PAPER first: his deployments stay PAPER; run N sessions (his choice) and compare each session's runner trades (My
   runner's copy) with ATP's own PAPER run of the same deployment (entries, exits, P&L, reasons). Any difference = a bug
   to report with both rows.
7. LIVE (only on his own words): his local LIVE file (`python -m app.runner live on`) + `live_trading` ON (owner
   organisation only; B1) + the deployment's own Live choice. The runner places LIVE only while ATP's signed state says
   LIVE is open for his organisation (fresh within `link_grace_minutes`); exits always run. Kill switch: his CLI
   `kill` / `resume` or his own button on My runner.

To verify in the pilot (from S5, and S4c / S8):
- the login with client code + PIN + TOTP from the VPS, and that the session lasts until midnight as documented;
- that orders are accepted from the registered static IP (and refused from another), and whether SmartAPI checks the
  `X-ClientPublicIP` header (the keystore's optional `public_ip`) against the registered IP;
- Angel One's error codes for a wrong PIN / TOTP (`AUTH_ERROR_CODES`); a refusal with another code reads "did not answer
  as expected" until listed;
- whether repeated wrong logins lock the account (the default is 2 attempts a day; keep retries low);
- the TOTP code at a 30-second boundary (one retry covers it);
- Angel One contract binding for his NFO deployments (S4b `ContractBinder`: same underlying / expiry / strike / right / lot);
- the LIVE state line in `link.log` ("ATP: LIVE open / not open for your organisation") after the owner's switch changes;
- PAPER parity on real bars over N sessions (S8 covers recorded bars only).

