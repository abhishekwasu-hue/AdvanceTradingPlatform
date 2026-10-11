# Compliance - India (SEBI / NSE retail-algo framework)

Rule-set `IN-SEBI` (`backend/app/compliance/rulesets/in_sebi.json`, version 1, in force from 2025-08-01). One row per rule:
what it implements, where the code enforces it, the tests that prove it, and the flag that switches enforcement on.
The JSON file is the source of truth (parameters live there, never in code); this page is generated from it
(`python backend/scripts/compliance_doc.py`) and `tests/test_compliance_rules.py` fails when it is out of date or a
named test does not exist. Design: `docs/design/D_SEBI.md`.

Status: **enforced** (code + tests), **partial** (some of it), **planned** (design only, PR order in the design note).

| Rule | What | Source | Status | Code | Tests | Flag |
|---|---|---|---|---|---|---|
| `IN-SEBI.algo_id.tag` | Every algo order carries the exchange-issued algo id | SEBI retail-algo circular; NSE implementation standards | enforced | `app/execution/tagging.py`<br>`app/execution/router.py`<br>`app/execution/multileg.py` | `test_live_router_tags_entry_and_stop_with_algo_id`<br>`test_exit_order_carries_algo_id`<br>`test_router_respects_broker_tag_limit` | - |
| `IN-SEBI.algo_id.required_for_live` | No LIVE order without an algo id | SEBI retail-algo circular | enforced | `app/execution/router.py` | `test_live_refused_without_algo_id_when_required` | `ALGO_ID_REQUIRED_FOR_LIVE` |
| `IN-SEBI.algo_id.registered_above_ops` | Above the OPS threshold the registered algo id is required (below it the broker's generic id) | SEBI retail-algo circular (OPS threshold set by the exchange) | planned | - | - | - |
| `IN-SEBI.ops.throttle` | Orders per second per client, exchange and segment - modify, cancel and stop included; exits served first | SEBI retail-algo circular; ADR-0004 (exits never blocked) | enforced | `app/execution/ops_throttle.py`<br>`app/brokers/rate_budget.py`<br>`app/workers/trading_worker.py` | `test_entries_beyond_the_rate_are_refused_before_the_broker_and_exits_are_never_refused`<br>`test_while_an_exit_waits_no_entry_takes_a_token`<br>`test_each_exchange_has_its_own_bucket`<br>`test_a_broker_429_pauses_that_exchange_and_a_success_resets_the_back_off` | `OPS_THROTTLE_ENABLED` |
| `IN-SEBI.api.rate_budget` | Per-tenant broker API budget (requests per second and minute) on every call | broker API limits | enforced | `app/brokers/rate_budget.py` | `test_rate_limited_broker_delegates_every_call_through_the_budget`<br>`test_worker_wraps_each_tenant_adapter_in_its_own_budget` | - |
| `IN-SEBI.order_type.market_protection` | Market and SL-M orders carry market protection where the broker requires it | exchange rule for algo orders; Kite / Upstox APIs | enforced | `app/execution/order_safety.py`<br>`app/brokers/zerodha.py`<br>`app/brokers/upstox.py` | `test_upstox_sends_market_protection_only_when_the_operator_sets_it` | `LIVE_MARKET_PROTECTION` |
| `IN-SEBI.order_type.policy` | Per-broker order-type policy for algos: MARKET refused or mapped to LIMIT/MPP; IOC / AMO refused where barred | SEBI retail-algo circular; broker policies | planned | - | - | - |
| `IN-SEBI.static_ip.registered` | API orders only from a static IP registered with the broker; primary + backup; limited changes | SEBI retail-algo circular; broker policies | planned | - | - | `STATIC_IP_REQUIRED_FOR_LIVE` |
| `IN-SEBI.login.daily` | Daily 2FA / OAuth login per broker session; no reliance on refresh tokens; pre-open reminder | SEBI retail-algo circular; broker session rules | partial | `app/brokers/token_lifecycle.py`<br>`app/brokers/routes.py` | `test_upstox_token_expires_at_next_0330_ist`<br>`test_live_deployment_never_fires_on_expired_token_but_paper_sibling_still_runs` | - |
| `IN-SEBI.golive.checklist` | Go-live checklist: vendor empanelment, strategy white-box/black-box class, AI disclosure / RA flag, DPDP | SEBI retail-algo circular; SEBI RA regulations; DPDP Act 2023 | partial | `app/platform/readiness.py` | - | - |
| `IN-SEBI.risk.futeq_mwpl` | FutEq / OI (MWPL) limits, expiry-day margin, lot sizes as of the trade date | NSE F&O position limits; SEBI margin framework | planned | - | - | - |

Parameters (current values in the JSON):

- `IN-SEBI.algo_id.tag`: `max_tag_length_default` = `20`
- `IN-SEBI.algo_id.registered_above_ops`: `ops_threshold` = `10`, `generic_id_source` = `broker`
- `IN-SEBI.ops.throttle`: `ops_per_second` = `10`, `counts` = `['place', 'modify', 'cancel', 'stop']`, `exit_lane_first` = `True`, `backoff_seconds` = `[0.5, 1, 2, 4]`, `critical_after_429s` = `3`
- `IN-SEBI.order_type.policy`: `market_for_algo` = `allow`, `allowed_validity` = `['DAY', 'IOC']`, `amo_allowed` = `True`
- `IN-SEBI.static_ip.registered`: `max_changes_per_week` = `1`, `backup_ip_allowed` = `True`
- `IN-SEBI.login.daily`: `reminder_minutes_before_open` = `30`
- `IN-SEBI.golive.checklist`: `dpdp_breach_notify_hours` = `72`, `log_retention_days` = `365`
- `IN-SEBI.risk.futeq_mwpl`: `expiry_day_margin_multiplier` = `1.0`
